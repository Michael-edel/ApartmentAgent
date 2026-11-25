"""Expose commercial monitor controls and receive catalog data from the browser extension."""
import logging
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, StrictBool

from app.commercial import (
    KRISHA_CATALOGS, commercial_monitor_enabled, commercial_monitor_status, eligible,
    save_scan, send_commercial_events, set_commercial_monitor_enabled,
)
from app.config import get_settings
from app.import_observability import observe
from app.krisha_search import KrishaSearchItem, normalize_krisha_listing_url

router = APIRouter(prefix="/api/v1/commercial", tags=["commercial"])
log = logging.getLogger(__name__)


class BrowserCommercialCard(BaseModel):
    url: str = Field(max_length=200)
    title: str = Field(min_length=1, max_length=300)
    price: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1200)
    published_at: AwareDatetime | None = None


class BrowserCommercialScan(BaseModel):
    catalog: str = Field(max_length=160)
    cards: list[BrowserCommercialCard] = Field(max_length=800)
    complete: StrictBool = True


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


@router.post("/browser/scan")
async def commercial_browser_scan(scan: BrowserCommercialScan, request: Request, tasks: BackgroundTasks):
    _check_extension_origin(request)
    settings = get_settings()
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
        items[url] = KrishaSearchItem(card.title, url, card.price, description=card.description,
                                      published_at=card.published_at)
    if not await save_scan(
        deal, list(items.values()), section=section, require_enabled=True, complete=scan.complete,
    ):
        raise HTTPException(409, "Мониторинг коммерческих объявлений выключен.")
    matching = sum(eligible(item, section) is not None for item in items.values())
    log.info("Krisha commercial browser catalog %s: %d matching of %d (%s)",
             scan.catalog, matching, len(items), "complete" if scan.complete else "partial")
    if scan.complete:
        tasks.add_task(observe, "krisha", scan.catalog, "browser", "success")
    tasks.add_task(send_commercial_events, settings)
    return {"ok": True, "catalog": scan.catalog, "matching": matching}
