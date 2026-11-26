"""Expose commercial monitor controls and receive catalog data from the browser extension."""
import logging
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, StrictBool

from app.commercial import (
    KRISHA_CATALOGS, PHOTO_BATCH_SIZE, KrishaCommercialItem, commercial_monitor_enabled,
    commercial_monitor_status, eligible, krisha_gallery, krisha_photo_candidates, save_scan,
    send_commercial_events, set_commercial_monitor_enabled,
)
from app.config import get_settings
from app.import_observability import observe
from app.krisha_search import normalize_krisha_listing_url
from app.source_photos import public_listing_photo

router = APIRouter(prefix="/api/v1/commercial", tags=["commercial"])
log = logging.getLogger("uvicorn.error").getChild(__name__)


class BrowserCommercialCard(BaseModel):
    url: str = Field(max_length=200)
    title: str = Field(min_length=1, max_length=300)
    price: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1200)
    published_at: AwareDatetime | None = None
    photo_url: str | None = Field(default=None, max_length=2048)
    photo_urls: list[Annotated[str, Field(max_length=2048)]] = Field(default_factory=list, max_length=30)


class BrowserPhotoDiagnostics(BaseModel):
    gallery_roots: int = Field(ge=0, le=100_000)
    primary_images: int = Field(ge=0, le=100_000)
    trusted_links: int = Field(ge=0, le=100_000)


class BrowserPhotoLookup(BaseModel):
    status: Literal["not_needed", "candidate_missing", "preflight_unavailable", "content_unavailable",
                    "http_unavailable", "redirect_or_fetch_error", "verification", "metadata_missing", "found",
                    "canonical_mismatch", "render_timeout"]
    url: str | None = Field(default=None, max_length=200)
    http_status: int | None = Field(default=None, ge=100, le=599)
    rendered: StrictBool = False
    diagnostics: BrowserPhotoDiagnostics | None = None


class BrowserCommercialScan(BaseModel):
    catalog: str = Field(max_length=160)
    cards: list[BrowserCommercialCard] = Field(max_length=800)
    complete: StrictBool = True
    photo_lookup: BrowserPhotoLookup | None = None
    photo_lookups: list[BrowserPhotoLookup] = Field(default_factory=list, max_length=PHOTO_BATCH_SIZE)


class BrowserCommercialReport(BaseModel):
    catalog: str = Field(max_length=160)
    status: Literal["verification", "unavailable", "error"]


class CommercialMonitorSettings(BaseModel):
    enabled: StrictBool


@router.get("/status")
async def get_commercial_monitor_status():
    return await commercial_monitor_status(get_settings())


@router.put("/settings")
async def update_commercial_monitor_settings(preference: CommercialMonitorSettings, request: Request):
    await set_commercial_monitor_enabled(preference.enabled)
    wakeup = getattr(request.app.state, "commercial_monitor_changed", None)
    if wakeup is not None:
        wakeup.set()
    return await commercial_monitor_status(get_settings())


@router.get("/browser/plan")
async def commercial_browser_plan():
    settings = get_settings()
    return {
        "enabled": await commercial_monitor_enabled(settings),
        "catalogs": list(KRISHA_CATALOGS.values()),
        "interval_minutes": settings.commercial_interval_minutes,
    }


def _check_extension_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    parsed = urlsplit(origin or "")
    if (origin and (parsed.scheme != "chrome-extension" or not parsed.netloc or parsed.path)
            or request.headers.get("sec-fetch-site") == "cross-site" and parsed.scheme != "chrome-extension"):
        raise HTTPException(403, "Передавайте данные через расширение ApartmentAgent.")


@router.post("/browser/report")
async def commercial_browser_report(report: BrowserCommercialReport, request: Request, tasks: BackgroundTasks):
    _check_extension_origin(request)
    if report.catalog not in KRISHA_CATALOGS.values():
        raise HTTPException(422, "Лента Krisha не входит в отбор по Экибастузу.")
    if not await commercial_monitor_enabled(get_settings()):
        raise HTTPException(409, "Мониторинг коммерческих объявлений выключен.")
    verification = report.status == "verification"
    tasks.add_task(observe, "krisha", report.catalog, "browser", "blocked" if verification else "error",
                   error="verification" if verification else None)
    log.info("Krisha commercial browser catalog %s: %s", report.catalog, report.status)
    return {"ok": True}


async def _catalog_items(scan: BrowserCommercialScan, request: Request, settings):
    _check_extension_origin(request)
    if not await commercial_monitor_enabled(settings):
        raise HTTPException(409, "Мониторинг коммерческих объявлений выключен.")
    route = next((key for key, url in KRISHA_CATALOGS.items() if url == scan.catalog), None)
    if route is None:
        raise HTTPException(422, "Лента Krisha не входит в отбор по Экибастузу.")
    section, deal = route
    items = {}
    for card in scan.cards:
        url = normalize_krisha_listing_url(card.url)
        if not url or url != card.url:
            raise HTTPException(422, "Некорректная ссылка на объявление Krisha.")
        items[url] = KrishaCommercialItem(card.title, url, card.price, description=card.description,
                                          published_at=card.published_at, photo_url=card.photo_url,
                                          photo_urls=card.photo_urls)
    return section, deal, list(items.values())


@router.post("/browser/photo-candidates")
async def commercial_browser_photo_candidates(scan: BrowserCommercialScan, request: Request):
    settings = get_settings()
    section, _, items = await _catalog_items(scan, request, settings)
    urls = await krisha_photo_candidates(items, section)
    log.info("Krisha commercial photo candidates for %s: %s", scan.catalog, ", ".join(urls) or "none")
    return {"urls": urls}


@router.post("/browser/scan")
async def commercial_browser_scan(scan: BrowserCommercialScan, request: Request, tasks: BackgroundTasks):
    settings = get_settings()
    section, deal, items = await _catalog_items(scan, request, settings)
    matching_items = [item for item in items if eligible(item, section) is not None]
    matching_urls = {item.url for item in matching_items}
    lookups = scan.photo_lookups or ([scan.photo_lookup] if scan.photo_lookup else [])
    attempted = {lookup.url for lookup in lookups
                 if lookup.url in matching_urls
                 and lookup.status not in {"not_needed", "candidate_missing", "preflight_unavailable"}}
    if not await save_scan(
        deal, items, section=section, require_enabled=True, complete=scan.complete, photo_attempts=attempted,
    ):
        raise HTTPException(409, "Мониторинг коммерческих объявлений выключен.")
    matching = len(matching_items)
    with_galleries = 0
    with_photos = 0
    for item in matching_items:
        has_gallery = bool(krisha_gallery(item))
        with_galleries += has_gallery
        with_photos += has_gallery or public_listing_photo(item.photo_url, "krisha") is not None
    log.info("Krisha commercial browser catalog %s: %d matching of %d, %d with photos, %d with galleries (%s)",
             scan.catalog, matching, len(items), with_photos, with_galleries, "complete" if scan.complete else "partial")
    for lookup in lookups:
        listing_url = lookup.url if lookup.url in matching_urls else None
        log.info("Krisha commercial photo lookup: %s, listing=%s, http=%s, rendered=%s, diagnostics=%s",
                 lookup.status, listing_url or "none", lookup.http_status or "none", lookup.rendered,
                 lookup.diagnostics.model_dump() if lookup.diagnostics is not None else "none")
    if scan.complete:
        tasks.add_task(observe, "krisha", scan.catalog, "browser", "success")
    tasks.add_task(send_commercial_events, settings)
    return {"ok": True, "catalog": scan.catalog, "matching": matching,
            "with_photos": with_photos, "with_galleries": with_galleries}
