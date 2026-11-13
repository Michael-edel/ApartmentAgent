import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from typing import Any, Literal
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import selectinload

from app import __version__
from app.author import OWNER_FROM_DESCRIPTION
from app.availability import listing_availability, set_listing_sold, set_source_availability, unavailable_urls
from app.bathroom_queue import router as bathroom_check_router
from app.ai_service import analyze_listing, current_analysis
from app.checker import ListingNotFound, check_all_listings, check_listing, periodic_checker
from app.config import get_settings
from app.database import SessionLocal, engine, initialize_database
from app.importer import ListingArchived, ListingImportError, ListingUnavailable, _address_from_text, _is_astana_coordinate_pair, _is_krisha_host, import_krisha_listing
from app.listing_service import ListingAlreadyExists, persist_listing
from app.market_comparison import LOOKBACK_DAYS, compare_saved_listings
from app.krisha_search import normalize_krisha_listing_url
from app.models import Listing, ListingCheck, ListingLike, PriceSnapshot, SearchHistory, SearchResult
from app.schemas import ListingCreate, ListingImportRequest, ListingResponse, PriceSnapshotResponse
from app.seller import seller_category
from app.scoring import assess_listing, listing_river_bank
from app.search_agent import InvalidSearchArea, InvalidSearchPrice, get_search_status, load_search_preference, periodic_search, run_search, bathrooms_for_row, _row_matches_basic, _row_matches_target
from app.search_analysis import analyze_search_result, is_area_in_range
from app.telegram import telegram_status

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database()
    await load_search_preference()

    stop_event = asyncio.Event()
    checker_task = asyncio.create_task(periodic_checker(stop_event))
    search_task = asyncio.create_task(periodic_search(stop_event))
    yield
    stop_event.set()
    await asyncio.gather(checker_task, search_task, return_exceptions=True)
    await engine.dispose()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
app.include_router(bathroom_check_router)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https://([a-z0-9-]+\.)*krisha\.kz$",
    allow_methods=["POST"],
    allow_headers=["content-type"],
)
_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
async def web_app() -> FileResponse:
    return FileResponse(_static_dir / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> FileResponse:
    return FileResponse(_static_dir / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse(_static_dir / "service-worker.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": app.version,
        "storage": "postgresql",
        "automatic_check_minutes": max(settings.check_interval_minutes, 15),
        "search_enabled": settings.search_enabled,
        "search_interval_minutes": max(settings.search_interval_minutes, 15),
        "search_providers": settings.search_providers,
        "twogis_enabled": settings.twogis_enabled,
        "twogis_configured": bool(settings.twogis_api_key),
    }


def _payload_from_row(row: Listing) -> ListingCreate:
    return ListingCreate(
        source=row.source,
        source_url=HttpUrl(row.source_url),
        title=row.title,
        description=row.description,
        repair_condition=row.repair_condition,
        repair_source_kind=row.repair_source_kind,
        repair_evidence=row.repair_evidence,
        mortgage_source_kind=row.mortgage_source_kind,
        mortgage_evidence=row.mortgage_evidence,
        property_details=row.property_details or {},
        details_extracted=row.details_imported_at is not None,
        city=row.city,
        district=row.district,
        residential_complex=row.residential_complex,
        complex_description=row.complex_description,
        complex_description_source_url=row.complex_description_source_url,
        complex_description_source_kind=row.complex_description_source_kind,
        address=row.address,
        latitude=row.latitude,
        longitude=row.longitude,
        price_kzt=row.price_kzt,
        area_m2=row.area_m2,
        rooms=row.rooms,
        bathrooms_count=row.bathrooms_count,
        bathrooms_count_is_lower_bound=row.bathrooms_count_is_lower_bound,
        floor=row.floor,
        floors_total=row.floors_total,
        building_year=row.building_year,
        building_type=row.building_type,
        twogis_name=row.twogis_name,
        twogis_rating=row.twogis_rating,
        twogis_review_count=row.twogis_review_count,
        twogis_url=row.twogis_url,
        is_full_two_room=row.is_full_two_room,
        mortgage_supported=row.mortgage_supported,
        is_pledged=row.is_pledged,
        krisha_district_price_per_m2=row.krisha_district_price_per_m2,
        krisha_city_price_per_m2=row.krisha_city_price_per_m2,
        krisha_comparison_district=row.krisha_comparison_district,
        seller_name=row.seller_name,
        seller_type=row.seller_type,
        photo_urls=list(row.photo_urls or []),
    )


def _price_history(row: Listing) -> list[PriceSnapshotResponse]:
    snapshots = sorted(row.prices or [], key=lambda item: item.observed_at)
    result: list[PriceSnapshotResponse] = []
    previous: PriceSnapshot | None = None
    for snapshot in snapshots:
        change = snapshot.price_kzt - previous.price_kzt if previous else None
        percent = round(change / previous.price_kzt * 100, 2) if change and previous else None
        result.append(
            PriceSnapshotResponse(
                price_kzt=snapshot.price_kzt,
                price_per_m2=snapshot.price_per_m2,
                observed_at=snapshot.observed_at,
                change_kzt=change,
                change_percent=percent,
            )
        )
        previous = snapshot
    return list(reversed(result))


def _is_blocked_import_error(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("огранич", "провер", "captcha", "403", "429"))


async def _finish_search_result_monitor(result_id: int, source_url: str) -> None:
    import_status = "error"
    import_error: str | None = None
    listing_id: int | None = None
    try:
        payload = await import_krisha_listing(source_url)
        saved = await persist_listing(payload, allow_existing=True, notify=True)
        import_status = "imported"
        listing_id = saved.row.id
    except (ListingArchived, ListingUnavailable) as exc:
        import_error = str(exc)
        import_status = "archived" if isinstance(exc, ListingArchived) else "unavailable"
    except ListingImportError as exc:
        import_error = str(exc)
        import_status = "blocked" if _is_blocked_import_error(import_error) else "error"
    except Exception as exc:  # noqa: BLE001
        import_error = str(exc)

    async with SessionLocal() as session:
        row = await session.get(SearchResult, result_id)
        if row is None:
            return
        if import_status in {"archived", "unavailable"}:
            await set_source_availability(session, source_url, state=import_status)
        row.import_status = import_status
        if listing_id is not None:
            row.imported_listing_id = listing_id
            listing = await session.get(Listing, listing_id)
            if listing is not None and listing.bathrooms_count is not None:
                row.bathrooms_count = listing.bathrooms_count
                row.bathrooms_count_is_lower_bound = listing.bathrooms_count_is_lower_bound
        row.import_error = import_error[:500] if import_error else None
        if import_status == "imported":
            row.imported_at = datetime.now(UTC)
        await session.commit()


def _favorite_key(url: str) -> str:
    return normalize_krisha_listing_url(url) or url


class LikeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    liked: bool = Field(strict=True)


async def _set_liked(session, url: str, liked: bool) -> None:
    key = _favorite_key(url)
    if liked:
        insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
        await session.execute(insert(ListingLike).values(url=key).on_conflict_do_nothing(index_elements=["url"]))
    else:
        await session.execute(delete(ListingLike).where(ListingLike.url == key))
    await session.commit()


def _to_response(row: Listing, *, liked: bool = False) -> ListingResponse:
    payload = _payload_from_row(row)
    assessment = assess_listing(payload, settings)
    return ListingResponse(
        id=row.id,
        liked=liked,
        availability=listing_availability(row.sold_at, row.source_archived_at, row.source_unavailable_at),
        sold_at=row.sold_at,
        source_archived_at=row.source_archived_at,
        source_unavailable_at=row.source_unavailable_at,
        river_bank=listing_river_bank(row.city, row.latitude, row.longitude),
        seller_category=seller_category(row.seller_type),
        created_at=row.created_at,
        assessment=assessment,
        ai_analysis=current_analysis(payload, assessment, row.ai_analysis),
        price_history=_price_history(row),
        complex_description_observed_at=row.complex_description_observed_at,
        details_imported_at=row.details_imported_at,
        krisha_comparison_observed_at=row.krisha_comparison_observed_at,
        krisha_comparison_source=row.krisha_comparison_source,
        **payload.model_dump(),
    )


async def _load_listing_response(listing_id: int) -> ListingResponse:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Listing)
            .options(selectinload(Listing.prices))
            .where(Listing.id == listing_id)
        )
        liked = row is not None and await session.get(ListingLike, _favorite_key(row.source_url)) is not None
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    return _to_response(row, liked=liked)


async def _save_listing(payload: ListingCreate, *, allow_existing: bool) -> ListingResponse:
    try:
        saved = await persist_listing(payload, allow_existing=allow_existing, notify=True)
    except ListingAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Listing with this URL already exists",
        ) from exc
    return await _load_listing_response(saved.row.id)


@app.post("/api/v1/listings", response_model=ListingResponse, status_code=status.HTTP_201_CREATED)
async def create_listing(payload: ListingCreate) -> ListingResponse:
    return await _save_listing(payload, allow_existing=False)


@app.post(
    "/api/v1/listings/import",
    response_model=ListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_listing(payload: ListingImportRequest) -> ListingResponse:
    try:
        listing_data = await import_krisha_listing(str(payload.source_url))
    except (ListingArchived, ListingUnavailable) as exc:
        source_state = "archived" if isinstance(exc, ListingArchived) else "unavailable"
        async with SessionLocal() as session:
            await set_source_availability(session, str(payload.source_url), state=source_state)
            await session.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except ListingImportError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return await _save_listing(listing_data, allow_existing=True)


@app.post(
    "/api/v1/listings/browser-import",
    response_model=ListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_listing_from_browser(payload: ListingCreate) -> ListingResponse:
    """Persist public data extracted by the Krisha-page bookmarklet.

    The browser script runs on the page the user opened normally. It sends
    only parsed public fields; cookies and page HTML never leave the browser.
    """
    parsed = urlparse(str(payload.source_url))
    if not _is_krisha_host((parsed.hostname or "").lower()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Разрешены только ссылки krisha.kz",
        )

    canonical_url = normalize_krisha_listing_url(str(payload.source_url))
    browser_payload = payload.model_copy(update={
        "source": "browser",
        "source_url": HttpUrl(canonical_url) if canonical_url else payload.source_url,
    })
    # Keep older installed extensions from replacing a house address with the
    # seller's office address while a browser queue is still running.
    own_address = _address_from_text(payload.title) if "м²" in payload.title else None
    if (own_address and any(char.isdigit() for char in own_address)
            and not payload.property_details.get("Адрес")
            and payload.title.lstrip()[:1].isdigit()):
        browser_payload = browser_payload.model_copy(update={"address": own_address})
    return await _save_listing(browser_payload, allow_existing=True)


class ArchivedListingRequest(ListingImportRequest):
    model_config = ConfigDict(extra="forbid")
    availability_status: Literal["archived"]


@app.post("/api/v1/listings/availability")
async def observe_archived_listing(payload: ArchivedListingRequest) -> dict[str, object]:
    source_url = str(payload.source_url)
    async with SessionLocal() as session:
        listing_id = await set_source_availability(session, source_url, state="archived")
        await session.commit()
    return {"id": listing_id, "source_url": source_url, "availability_status": "archived"}


@app.post("/api/v1/checks/run")
async def run_checks_now() -> dict[str, int]:
    summary = await check_all_listings()
    return {
        "checked": summary.checked,
        "updated": summary.updated,
        "blocked": summary.blocked,
        "errors": summary.errors,
        "archived": summary.archived,
        "unavailable": summary.unavailable,
    }


@app.post("/api/v1/listings/{listing_id}/check")
async def check_one_listing(listing_id: int) -> dict[str, object]:
    try:
        result = await check_listing(listing_id)
    except ListingNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found") from exc
    return {
        "listing_id": result.listing_id,
        "status": result.status,
        "message": result.message,
        "old_price_kzt": result.old_price_kzt,
        "new_price_kzt": result.new_price_kzt,
        "updated": result.updated,
    }


@app.get("/api/v1/notifications/status")
async def notifications_status() -> dict[str, object]:
    return await telegram_status()


@app.get("/api/v1/checks/latest")
async def latest_checks() -> list[dict[str, object]]:
    async with SessionLocal() as session:
        rows = (
            await session.scalars(select(ListingCheck).order_by(ListingCheck.checked_at.desc()).limit(100))
        ).all()
    return [
        {
            "listing_id": row.listing_id,
            "status": row.status,
            "message": row.message,
            "old_price_kzt": row.old_price_kzt,
            "new_price_kzt": row.new_price_kzt,
            "checked_at": row.checked_at,
        }
        for row in rows
    ]


class SearchRunRequest(BaseModel):
    rooms: Literal[2, 3, 4]
    min_price_kzt: int | None = Field(default=None, strict=True, ge=1, le=10_000_000_000)
    max_price_kzt: int | None = Field(default=None, strict=True, ge=1, le=10_000_000_000)
    min_area_m2: float | None = Field(default=None, strict=True, ge=0.1, le=10000, allow_inf_nan=False)
    max_area_m2: float | None = Field(default=None, strict=True, ge=0.1, le=10000, allow_inf_nan=False)
    min_bathrooms: Literal[0, 2] | None = None
    exclude_pledged: bool | None = None
    seller: Literal["owner"] | None = None

    @model_validator(mode="after")
    def validate_area(self) -> "SearchRunRequest":
        if "min_area_m2" in self.model_fields_set and self.min_area_m2 is None:
            raise ValueError("Укажите минимальную площадь")
        if self.min_area_m2 is not None and self.max_area_m2 is not None and self.min_area_m2 > self.max_area_m2:
            raise ValueError("Минимальная площадь не может превышать максимальную")
        return self


@app.post("/api/v1/search/run")
async def run_search_now(request: SearchRunRequest | None = None) -> dict[str, object]:
    try:
        if request is None:
            summary = await run_search()
        else:
            summary = await run_search(
                request.rooms, request.max_price_kzt, request.min_area_m2, request.max_area_m2,
                request.min_bathrooms, request.exclude_pledged,
                max_area_m2_is_set="max_area_m2" in request.model_fields_set,
                seller=request.seller,
                min_price_kzt=request.min_price_kzt,
                min_price_kzt_is_set="min_price_kzt" in request.model_fields_set,
            )
    except (InvalidSearchArea, InvalidSearchPrice) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "queries": summary.queries,
        "found": summary.found,
        "new": summary.new,
        "errors": summary.errors,
        "blocked": summary.blocked,
        "imported": summary.imported,
        "import_errors": summary.import_errors,
        "import_blocked": summary.import_blocked,
        "providers": summary.providers,
        "exclude_pledged": settings.exclude_pledged,
    }


@app.get("/api/v1/search/status")
async def search_status() -> dict[str, object]:
    return await get_search_status()


@app.get("/api/v1/comparison/liked")
async def comparison_liked() -> list[dict[str, object]]:
    """Return all favorites, preferring a saved card to a discovered duplicate."""
    async with SessionLocal() as session:
        liked_urls = set(await session.scalars(select(ListingLike.url)))
        if not liked_urls:
            return []

        saved = (await session.execute(select(
            Listing.id, Listing.source_url, Listing.title, Listing.price_kzt, Listing.area_m2,
            Listing.rooms, Listing.address, Listing.district, Listing.city, Listing.latitude,
            Listing.longitude, Listing.floor, Listing.floors_total, Listing.bathrooms_count,
            Listing.bathrooms_count_is_lower_bound, Listing.repair_condition,
            Listing.mortgage_supported, Listing.is_pledged, Listing.seller_type,
        ).order_by(Listing.created_at.desc(), Listing.id.desc()))).all()
        by_url: dict[str, dict[str, object]] = {}
        for row in saved:
            key = _favorite_key(row.source_url)
            if key not in liked_urls or key in by_url:
                continue
            by_url[key] = {
                'source_url': key, 'source': 'saved', 'listing_id': row.id,
                'search_result_id': None, 'liked': True, 'title': row.title,
                'price_kzt': row.price_kzt, 'price_lower_bound_kzt': None,
                'price_is_lower_bound': False,
                'price_per_m2': round(row.price_kzt / row.area_m2) if row.area_m2 else None,
                'area_m2': row.area_m2, 'rooms': row.rooms,
                'address': row.address, 'district': row.district, 'city': row.city,
                'river_bank': listing_river_bank(row.city, row.latitude, row.longitude),
                'floor': row.floor, 'floors_total': row.floors_total,
                'bathrooms_count': row.bathrooms_count,
                'bathrooms_count_is_lower_bound': bool(row.bathrooms_count_is_lower_bound),
                'repair_condition': row.repair_condition,
                'mortgage_supported': row.mortgage_supported, 'is_pledged': row.is_pledged,
                'seller_type': row.seller_type, 'seller_category': seller_category(row.seller_type),
            }

        missing = liked_urls - by_url.keys()
        if missing:
            results = await session.stream_scalars(
                select(SearchResult).order_by(SearchResult.first_seen.desc(), SearchResult.id.desc())
                .execution_options(yield_per=200)
            )
            async for row in results:
                key = _favorite_key(row.url)
                if key not in missing:
                    continue
                analysis = analyze_search_result(
                    row.title or '', row.snippet, max_price=settings.max_price_kzt,
                    min_price=settings.min_price_kzt, min_area=settings.min_area_m2,
                    max_area=settings.max_area_m2, target_rooms=settings.target_rooms,
                    mortgage_supported=row.mortgage_supported, is_pledged=row.is_pledged,
                )
                count = bathrooms_for_row(row)
                by_url[key] = {
                    'source_url': key, 'source': 'found', 'listing_id': None,
                    'search_result_id': row.id, 'liked': True, 'title': row.title,
                    'price_kzt': analysis['price_kzt'],
                    'price_lower_bound_kzt': analysis['price_lower_bound_kzt'],
                    'price_is_lower_bound': analysis['price_is_lower_bound'],
                    'price_per_m2': analysis['price_per_m2'], 'area_m2': analysis['area_m2'],
                    'rooms': analysis['rooms'], 'address': None, 'district': None, 'city': None,
                    'river_bank': 'unknown', 'floor': analysis['floor'],
                    'floors_total': analysis['floors_total'], 'bathrooms_count': count,
                    'bathrooms_count_is_lower_bound': bool(
                        row.bathrooms_count_is_lower_bound or analysis['bathrooms_count_is_lower_bound']
                    ) if count is not None else False,
                    'repair_condition': None, 'mortgage_supported': analysis['mortgage_supported'],
                    'is_pledged': analysis['is_pledged'], 'seller_type': row.seller_type,
                    'seller_category': seller_category(row.seller_type),
                }
                missing.remove(key)
                if not missing:
                    break
    return list(by_url.values())


@app.get("/api/v1/search/map")
async def search_map(
    seller: Literal['all', 'owner', 'verified_agent', 'realtor', 'unknown'] = 'all',
    bank: Literal['all', 'left'] = 'all',
) -> dict[str, object]:
    """Map current matches with actual saved pins; count other matches separately."""
    async with SessionLocal() as session:
        unavailable = await unavailable_urls(session)
        saved_rows = (await session.execute(select(
            Listing.id, Listing.source_url, Listing.title, Listing.city, Listing.address,
            Listing.district, Listing.latitude, Listing.longitude, Listing.rooms, Listing.area_m2,
            Listing.price_kzt, Listing.bathrooms_count, Listing.is_pledged, Listing.seller_type,
        ).order_by(Listing.created_at.desc(), Listing.id.desc()))).all()
        saved: dict[str, Any] = {}
        for row in saved_rows:
            saved.setdefault(_favorite_key(row.source_url), row)
        found: dict[str, SearchResult] = {}
        stream = await session.stream_scalars(
            select(SearchResult).order_by(SearchResult.first_seen.desc(), SearchResult.id.desc())
            .execution_options(yield_per=200)
        )
        async for row in stream:
            found.setdefault(_favorite_key(row.url), row)

    items: list[dict[str, object]] = []
    without_coordinates = 0
    for key in dict.fromkeys([*saved, *found]):
        if key in unavailable:
            continue
        listing = saved.get(key)
        result = found.get(key)
        if listing is not None:
            if (listing.city or '').strip().casefold() not in {'астана', 'astana'}:
                continue
            if not listing.area_m2 or not listing.price_kzt or not listing.rooms:
                continue
            if listing.rooms < settings.target_rooms or not is_area_in_range(
                listing.area_m2, settings.min_area_m2, settings.max_area_m2
            ) or listing.price_kzt > settings.max_price_kzt or (
                settings.min_price_kzt is not None and listing.price_kzt < settings.min_price_kzt
            ):
                continue
            if settings.min_bathrooms and (listing.bathrooms_count is None or listing.bathrooms_count < settings.min_bathrooms):
                continue
            if settings.exclude_pledged and listing.is_pledged is True:
                continue
            role = listing.seller_type
            if (not role or not role.strip() or role.strip() == OWNER_FROM_DESCRIPTION) and result is not None:
                discovery = result.seller_type
                if seller_category(discovery) != 'unknown':
                    role = discovery
            category = seller_category(role)
            if seller != 'all' and category != seller:
                continue
            if not _is_astana_coordinate_pair(listing.latitude, listing.longitude):
                without_coordinates += 1
                continue
            river_bank = listing_river_bank(listing.city, listing.latitude, listing.longitude)
            if bank == 'left' and river_bank != 'left':
                continue
            items.append({
                'source_url': key, 'title': listing.title, 'latitude': listing.latitude,
                'longitude': listing.longitude, 'price_kzt': listing.price_kzt,
                'address': listing.address, 'district': listing.district,
                'listing_id': listing.id, 'search_result_id': result.id if result else None,
                'river_bank': river_bank, 'seller_category': category,
            })
        elif result is not None:
            if not _row_matches_target(result):
                continue
            if seller != 'all' and seller_category(result.seller_type) != seller:
                continue
            without_coordinates += 1
    return {'items': items, 'without_coordinates': without_coordinates}


@app.get("/api/v1/search/results")
async def search_results(
    limit: int = 50,
    only_new: bool = False,
    include_unconfirmed: bool = False,
    sort: Literal["newest", "score_desc", "score_asc", "price_asc", "price_desc",
                  "price_m2_asc", "price_m2_desc", "area_asc", "area_desc", "seller", "liked"] = "newest",
    seller: Literal["all", "owner", "verified_agent", "realtor", "unknown"] = "all",
    bank: Literal["all", "left"] = "all",
) -> list[dict[str, object]]:
    safe_limit = min(max(limit, 1), 200)
    async with SessionLocal() as session:
        unavailable = await unavailable_urls(session)
        query = select(SearchResult).order_by(SearchResult.first_seen.desc(), SearchResult.id.desc())
        if only_new:
            query = query.where(SearchResult.status.like("new:%"))

        def matches_filter(row: SearchResult) -> bool:
            if _favorite_key(row.url) in unavailable:
                return False
            if include_unconfirmed:
                return _row_matches_basic(row) and settings.min_bathrooms > 0 and bathrooms_for_row(row) is None
            return _row_matches_target(row)

        rows = []
        if sort == "newest" and seller == "all" and bank == "all":
            offset = 0
            batch_size = 200
            while len(rows) < safe_limit:
                batch = (await session.scalars(query.offset(offset).limit(batch_size))).all()
                for row in batch:
                    if matches_filter(row):
                        rows.append(row)
                        if len(rows) == safe_limit:
                            break
                offset += len(batch)
                if len(batch) < batch_size:
                    break
        else:
            # A single ordered query avoids offset shifts when new rows arrive.
            stream = await session.stream_scalars(query.execution_options(yield_per=200))
            rows = [row async for row in stream if matches_filter(row)]

        listing_ids = list({row.imported_listing_id for row in rows if row.imported_listing_id is not None})
        sellers: dict[int, str | None] = {}
        banks: dict[int, Literal['left', 'right', 'unknown']] = {}
        for start in range(0, len(listing_ids), 500):
            values = await session.execute(
                select(Listing.id, Listing.seller_type, Listing.city, Listing.latitude, Listing.longitude)
                .where(Listing.id.in_(listing_ids[start:start + 500]))
            )
            for listing_id, seller_type, city, latitude, longitude in values.all():
                sellers[listing_id] = seller_type
                banks[listing_id] = listing_river_bank(city, latitude, longitude)
        def effective_seller_type(row: SearchResult) -> str | None:
            linked = sellers.get(row.imported_listing_id)
            if linked and linked.strip() and linked.strip() != OWNER_FROM_DESCRIPTION:
                return linked
            discovery = getattr(row, "seller_type", None)
            return discovery if seller_category(discovery) != "unknown" else linked
        if seller != "all":
            rows = [row for row in rows if seller_category(effective_seller_type(row)) == seller]
        if bank == "left":
            rows = [row for row in rows if banks.get(row.imported_listing_id, "unknown") == "left"]
        liked_urls = set(await session.scalars(select(ListingLike.url)))

    result: list[dict[str, object]] = []
    for row in rows:
        river_bank = banks.get(row.imported_listing_id, "unknown")
        analysis = analyze_search_result(
            row.title or "",
            row.snippet,
            max_price=settings.max_price_kzt,
            min_price=settings.min_price_kzt,
            min_area=settings.min_area_m2,
            max_area=settings.max_area_m2,
            target_rooms=settings.target_rooms,
            mortgage_supported=getattr(row, "mortgage_supported", None),
            is_pledged=getattr(row, "is_pledged", None),
            river_bank=river_bank,
        )
        analysis["bathrooms_count"] = bathrooms_for_row(row)
        analysis["bathrooms_count_is_lower_bound"] = row.bathrooms_count_is_lower_bound or analysis["bathrooms_count_is_lower_bound"]
        result.append(
            {
                "id": row.id,
                "url": row.url,
                "liked": _favorite_key(row.url) in liked_urls,
                "river_bank": river_bank,
                "search_engine": row.search_engine,
                "query": row.query,
                "title": row.title,
                "snippet": row.snippet,
                "status": row.status.split(":", 1)[0],
                "priority": "urgent" if analysis["score"] >= 85 else "good" if analysis["score"] >= 65 else "normal",
                "import_status": row.import_status,
                "imported_listing_id": row.imported_listing_id,
                "seller_type": effective_seller_type(row),
                "seller_category": seller_category(effective_seller_type(row)),
                "import_error": row.import_error,
                "imported_at": row.imported_at,
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
                "analysis": analysis,
            }
        )
    if sort == "liked":
        result.sort(key=lambda item: not item["liked"])
    elif sort == "seller":
        ranks = {"owner": 0, "verified_agent": 1, "realtor": 2, "unknown": 3}
        result.sort(key=lambda item: ranks.get(str(item["seller_category"]), 3))
    elif sort != "newest":
        fields = {"score": "score", "price": "price_kzt", "price_m2": "price_per_m2", "area": "area_m2"}
        field_name, direction = sort.rsplit("_", 1)
        analysis_key = fields[field_name]

        def sort_key(item: dict[str, object]) -> tuple[bool, float]:
            analysis = item["analysis"]
            assert isinstance(analysis, dict)
            value = analysis[analysis_key]
            if not isinstance(value, (int, float)):
                return True, 0
            return False, -value if direction == "desc" else value

        # Python's stable sort preserves the existing newest/id order for ties.
        result.sort(key=sort_key)
    return result[:safe_limit]


@app.put("/api/v1/search/results/{result_id}/liked")
async def set_search_result_liked(result_id: int, payload: LikeRequest) -> dict[str, bool]:
    async with SessionLocal() as session:
        row = await session.get(SearchResult, result_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search result not found")
        await _set_liked(session, row.url, payload.liked)
    return {"liked": payload.liked}


@app.post("/api/v1/search/results/{result_id}/seen")
async def mark_search_result_seen(result_id: int) -> dict[str, object]:
    async with SessionLocal() as session:
        row = await session.get(SearchResult, result_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search result not found")
        priority = row.status.split(":", 1)[1] if ":" in row.status else "normal"
        row.status = f"seen:{priority}"
        await session.commit()
    return {"id": result_id, "status": "seen", "priority": priority}


@app.post("/api/v1/search/results/{result_id}/monitor", status_code=status.HTTP_202_ACCEPTED)
async def monitor_search_result(
    result_id: int,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    """Start monitoring before opening a search result in the browser.

    The server tries to import the listing in the background. If Krisha blocks
    the server, the browser extension/bookmarklet can complete the same import
    after the user opens the public listing page.
    """
    async with SessionLocal() as session:
        row = await session.get(SearchResult, result_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search result not found")

        priority = row.status.split(":", 1)[1] if ":" in row.status else "normal"
        row.status = f"seen:{priority}"
        should_schedule = row.import_status not in {"imported", "queued"}
        if should_schedule:
            row.import_status = "queued"
            row.import_error = None
        source_url = row.url
        imported_listing_id = row.imported_listing_id
        await session.commit()

    if should_schedule:
        background_tasks.add_task(_finish_search_result_monitor, result_id, source_url)
    return {
        "id": result_id,
        "status": "monitoring_started",
        "import_status": "queued" if should_schedule else "imported",
        "imported_listing_id": imported_listing_id,
    }


@app.get("/api/v1/search/history")
async def search_history(limit: int = 50) -> list[dict[str, object]]:
    safe_limit = min(max(limit, 1), 200)
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(SearchHistory).order_by(SearchHistory.searched_at.desc()).limit(safe_limit)
            )
        ).all()
    return [
        {
            "search_engine": row.search_engine,
            "query": row.query,
            "searched_at": row.searched_at,
            "total_found": row.total_found,
            "new_found": row.new_found,
            "status": row.status,
            "message": row.message,
        }
        for row in rows
    ]


@app.get("/api/v1/listings", response_model=list[ListingResponse])
async def list_listings(
    min_score: int | None = None,
    seller: Literal["all", "owner", "verified_agent", "realtor", "unknown"] = "all",
) -> list[ListingResponse]:
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(Listing)
                .options(selectinload(Listing.prices))
                .order_by(Listing.created_at.desc())
            )
        ).all()
        liked_urls = set(await session.scalars(select(ListingLike.url)))
    items = [_to_response(row, liked=_favorite_key(row.source_url) in liked_urls) for row in rows]
    if seller != "all":
        items = [item for item in items if item.seller_category == seller]
    if min_score is not None:
        items = [item for item in items if item.assessment.score >= min_score]
    return items


@app.get("/api/v1/listings/{listing_id}/comparison")
async def listing_comparison(listing_id: int, include_comparables: bool = False) -> dict[str, object]:
    from app.availability import listing_is_available

    now = datetime.now(UTC)
    columns = (
        Listing.id, Listing.source_url, Listing.city, Listing.district,
        Listing.price_kzt, Listing.area_m2, Listing.last_imported_at,
        Listing.krisha_district_price_per_m2, Listing.krisha_city_price_per_m2,
        Listing.krisha_comparison_observed_at, Listing.krisha_comparison_source,
        Listing.krisha_comparison_district,
    )
    if include_comparables:
        columns += (Listing.title, Listing.address, Listing.residential_complex, Listing.rooms,
                    Listing.latitude, Listing.longitude, Listing.floor, Listing.floors_total,
                    Listing.bathrooms_count, Listing.bathrooms_count_is_lower_bound, Listing.photo_urls, Listing.seller_type,
                    Listing.sold_at, Listing.source_archived_at, Listing.source_unavailable_at,
                    Listing.updated_at, Listing.created_at)
    async with SessionLocal() as session:
        target = (await session.execute(select(*columns).where(Listing.id == listing_id))).first()
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
        rows = (await session.execute(
            select(*columns).where(Listing.last_imported_at >= now - timedelta(days=LOOKBACK_DAYS),
                                   listing_is_available())
        )).all()
        comparable_rows = (await session.execute(select(*columns))).all() if include_comparables else None
    return compare_saved_listings(target, rows, now=now, include_comparables=include_comparables,
                                  comparable_rows=comparable_rows)


@app.get("/api/v1/listings/{listing_id}/duplicates")
async def listing_duplicates(listing_id: int) -> dict[str, object]:
    from app.property_duplicates import group_listings, unique_offers

    columns = (Listing.id, Listing.source_url, Listing.title, Listing.city, Listing.address,
               Listing.rooms, Listing.area_m2, Listing.floor, Listing.floors_total,
               Listing.bathrooms_count, Listing.bathrooms_count_is_lower_bound, Listing.photo_urls, Listing.price_kzt, Listing.seller_type,
               Listing.sold_at, Listing.source_archived_at, Listing.source_unavailable_at,
               Listing.last_imported_at, Listing.created_at, Listing.updated_at)
    async with SessionLocal() as session:
        rows = (await session.execute(select(*columns))).all()
    target = next((row for row in rows if row.id == listing_id), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    group = next(group for group in group_listings(rows, target=target) if any(row.id == listing_id for row in group))
    items = unique_offers(group, target=target)
    return {'listing_id': listing_id, 'status': 'probable' if len(items) > 1 else 'none', 'items': items}


@app.get("/api/v1/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(listing_id: int) -> ListingResponse:
    return await _load_listing_response(listing_id)


@app.put("/api/v1/listings/{listing_id}/liked")
async def set_listing_liked(listing_id: int, payload: LikeRequest) -> dict[str, bool]:
    async with SessionLocal() as session:
        row = await session.get(Listing, listing_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
        await _set_liked(session, row.source_url, payload.liked)
    return {"liked": payload.liked}


class SoldListingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sold: bool = Field(strict=True)


@app.put("/api/v1/listings/{listing_id}/sold", response_model=ListingResponse)
async def update_listing_sold(listing_id: int, payload: SoldListingRequest) -> ListingResponse:
    async with SessionLocal() as session:
        listing = await session.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
        await set_listing_sold(session, listing.source_url, payload.sold)
        await session.commit()
    return await _load_listing_response(listing_id)


@app.get("/api/v1/listings/{listing_id}/price-history")
async def listing_price_history(listing_id: int) -> list[PriceSnapshotResponse]:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Listing)
            .options(selectinload(Listing.prices))
            .where(Listing.id == listing_id)
        )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    return _price_history(row)


@app.post("/api/v1/listings/{listing_id}/ai-analysis", response_model=ListingResponse)
async def refresh_listing_ai_analysis(listing_id: int) -> ListingResponse:
    async with SessionLocal() as session:
        row = await session.scalar(select(Listing).where(Listing.id == listing_id))
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
        payload = _payload_from_row(row)
        analysis = await analyze_listing(payload, assess_listing(payload, settings))
        row.ai_analysis = analysis
        row.ai_analyzed_at = datetime.now(UTC)
        await session.commit()
    return await _load_listing_response(listing_id)
