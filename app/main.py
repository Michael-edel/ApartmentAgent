import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, model_validator
from typing import Literal
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import __version__
from app.bathroom_queue import router as bathroom_check_router
from app.ai_service import analyze_listing, current_analysis
from app.checker import ListingNotFound, check_all_listings, check_listing, periodic_checker
from app.config import get_settings
from app.database import SessionLocal, engine, initialize_database
from app.importer import ListingImportError, _address_from_text, _is_krisha_host, import_krisha_listing
from app.listing_service import ListingAlreadyExists, persist_listing
from app.market_comparison import LOOKBACK_DAYS, compare_saved_listings
from app.krisha_search import normalize_krisha_listing_url
from app.models import Listing, ListingCheck, PriceSnapshot, SearchHistory, SearchResult
from app.schemas import ListingCreate, ListingImportRequest, ListingResponse, PriceSnapshotResponse
from app.scoring import assess_listing
from app.search_agent import get_search_status, load_search_preference, periodic_search, run_search, bathrooms_for_row, _row_matches_basic, _row_matches_target
from app.search_analysis import analyze_search_result
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
    except ListingImportError as exc:
        import_error = str(exc)
        import_status = "blocked" if _is_blocked_import_error(import_error) else "error"
    except Exception as exc:  # noqa: BLE001
        import_error = str(exc)

    async with SessionLocal() as session:
        row = await session.get(SearchResult, result_id)
        if row is None:
            return
        row.import_status = import_status
        row.imported_listing_id = listing_id
        if listing_id is not None:
            listing = await session.get(Listing, listing_id)
            if listing is not None and listing.bathrooms_count is not None:
                row.bathrooms_count = listing.bathrooms_count
                row.bathrooms_count_is_lower_bound = listing.bathrooms_count_is_lower_bound
        row.import_error = import_error[:500] if import_error else None
        row.imported_at = datetime.now(UTC) if import_status == "imported" else None
        await session.commit()


def _to_response(row: Listing) -> ListingResponse:
    payload = _payload_from_row(row)
    assessment = assess_listing(payload, settings)
    return ListingResponse(
        id=row.id,
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
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    return _to_response(row)


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


@app.post("/api/v1/checks/run")
async def run_checks_now() -> dict[str, int]:
    summary = await check_all_listings()
    return {
        "checked": summary.checked,
        "updated": summary.updated,
        "blocked": summary.blocked,
        "errors": summary.errors,
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
    max_price_kzt: int | None = Field(default=None, strict=True, ge=1, le=10_000_000_000)
    min_area_m2: float | None = Field(default=None, strict=True, ge=0.1, le=10000, allow_inf_nan=False)
    max_area_m2: float | None = Field(default=None, strict=True, ge=0.1, le=10000, allow_inf_nan=False)
    min_bathrooms: Literal[0, 2] | None = None

    @model_validator(mode="after")
    def validate_area(self) -> "SearchRunRequest":
        if (self.min_area_m2 is None) != (self.max_area_m2 is None):
            raise ValueError("Укажите минимальную и максимальную площадь")
        if self.min_area_m2 is not None and self.max_area_m2 is not None and self.min_area_m2 > self.max_area_m2:
            raise ValueError("Минимальная площадь не может превышать максимальную")
        return self


@app.post("/api/v1/search/run")
async def run_search_now(request: SearchRunRequest | None = None) -> dict[str, object]:
    summary = await run_search(
        request.rooms if request is not None else None,
        request.max_price_kzt if request is not None else None,
        request.min_area_m2 if request is not None else None,
        request.max_area_m2 if request is not None else None,
        request.min_bathrooms if request is not None else None,
    )
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
    }


@app.get("/api/v1/search/status")
async def search_status() -> dict[str, object]:
    return await get_search_status()


@app.get("/api/v1/search/results")
async def search_results(limit: int = 50, only_new: bool = False, include_unconfirmed: bool = False) -> list[dict[str, object]]:
    safe_limit = min(max(limit, 1), 200)
    async with SessionLocal() as session:
        query = select(SearchResult).order_by(SearchResult.first_seen.desc(), SearchResult.id.desc())
        if only_new:
            query = query.where(SearchResult.status.like("new:%"))
        rows = []
        offset = 0
        batch_size = 200
        while len(rows) < safe_limit:
            batch = (await session.scalars(query.offset(offset).limit(batch_size))).all()
            if not batch:
                break
            for row in batch:
                matches = (_row_matches_basic(row) and settings.min_bathrooms > 0 and bathrooms_for_row(row) is None) if include_unconfirmed else _row_matches_target(row)
                if matches:
                    rows.append(row)
                    if len(rows) == safe_limit:
                        break
            offset += len(batch)
            if len(batch) < batch_size:
                break

    result: list[dict[str, object]] = []
    for row in rows:
        analysis = analyze_search_result(
            row.title or "",
            row.snippet,
            max_price=settings.max_price_kzt,
            min_area=settings.min_area_m2,
            max_area=settings.max_area_m2,
            target_rooms=settings.target_rooms,
            mortgage_supported=getattr(row, "mortgage_supported", None),
        )
        analysis["bathrooms_count"] = bathrooms_for_row(row)
        analysis["bathrooms_count_is_lower_bound"] = row.bathrooms_count_is_lower_bound or analysis["bathrooms_count_is_lower_bound"]
        result.append(
            {
                "id": row.id,
                "url": row.url,
                "search_engine": row.search_engine,
                "query": row.query,
                "title": row.title,
                "snippet": row.snippet,
                "status": row.status.split(":", 1)[0],
                "priority": "urgent" if analysis["score"] >= 85 else "good" if analysis["score"] >= 65 else "normal",
                "import_status": row.import_status,
                "imported_listing_id": row.imported_listing_id,
                "import_error": row.import_error,
                "imported_at": row.imported_at,
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
                "analysis": analysis,
            }
        )
    return result


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
async def list_listings(min_score: int | None = None) -> list[ListingResponse]:
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(Listing)
                .options(selectinload(Listing.prices))
                .order_by(Listing.created_at.desc())
            )
        ).all()
    items = [_to_response(row) for row in rows]
    if min_score is not None:
        items = [item for item in items if item.assessment.score >= min_score]
    return items


@app.get("/api/v1/listings/{listing_id}/comparison")
async def listing_comparison(listing_id: int) -> dict[str, object]:
    now = datetime.now(UTC)
    columns = (
        Listing.id, Listing.source_url, Listing.city, Listing.district,
        Listing.price_kzt, Listing.area_m2, Listing.last_imported_at,
        Listing.krisha_district_price_per_m2, Listing.krisha_city_price_per_m2,
        Listing.krisha_comparison_observed_at, Listing.krisha_comparison_source,
        Listing.krisha_comparison_district,
    )
    async with SessionLocal() as session:
        target = (await session.execute(select(*columns).where(Listing.id == listing_id))).first()
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
        rows = (await session.execute(
            select(*columns).where(Listing.last_imported_at >= now - timedelta(days=LOOKBACK_DAYS))
        )).all()
    return compare_saved_listings(target, rows, now=now)


@app.get("/api/v1/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(listing_id: int) -> ListingResponse:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Listing)
            .options(selectinload(Listing.prices))
            .where(Listing.id == listing_id)
        )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    return _to_response(row)


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
