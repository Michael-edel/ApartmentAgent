import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai_service import analyze_listing
from app.checker import ListingNotFound, check_all_listings, check_listing, periodic_checker
from app.config import get_settings
from app.database import SessionLocal, engine, initialize_database
from app.importer import ListingImportError, _is_krisha_host, import_krisha_listing
from app.listing_service import ListingAlreadyExists, persist_listing
from app.models import Listing, ListingCheck, PriceSnapshot, SearchHistory, SearchResult
from app.schemas import ListingCreate, ListingImportRequest, ListingResponse, PriceSnapshotResponse
from app.scoring import assess_listing
from app.search_agent import get_search_status, periodic_search, run_search
from app.search_analysis import analyze_search_result
from app.telegram import telegram_status

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database()

    stop_event = asyncio.Event()
    checker_task = asyncio.create_task(periodic_checker(stop_event))
    search_task = asyncio.create_task(periodic_search(stop_event))
    yield
    stop_event.set()
    await asyncio.gather(checker_task, search_task, return_exceptions=True)
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.11.0", lifespan=lifespan)
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
    return FileResponse(_static_dir / "index.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> FileResponse:
    return FileResponse(_static_dir / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/service-worker.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse(_static_dir / "service-worker.js", media_type="application/javascript")


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": "0.11.0",
        "storage": "postgresql",
        "automatic_check_minutes": max(settings.check_interval_minutes, 15),
        "search_enabled": settings.search_enabled,
        "search_interval_minutes": max(settings.search_interval_minutes, 15),
        "search_providers": settings.search_providers,
    }


def _payload_from_row(row: Listing) -> ListingCreate:
    return ListingCreate(
        source=row.source,
        source_url=HttpUrl(row.source_url),
        title=row.title,
        description=row.description,
        city=row.city,
        district=row.district,
        residential_complex=row.residential_complex,
        price_kzt=row.price_kzt,
        area_m2=row.area_m2,
        rooms=row.rooms,
        floor=row.floor,
        floors_total=row.floors_total,
        building_year=row.building_year,
        building_type=row.building_type,
        is_full_two_room=row.is_full_two_room,
        mortgage_supported=row.mortgage_supported,
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


def _to_response(row: Listing) -> ListingResponse:
    payload = _payload_from_row(row)
    return ListingResponse(
        id=row.id,
        created_at=row.created_at,
        assessment=assess_listing(payload, settings),
        ai_analysis=row.ai_analysis,
        price_history=_price_history(row),
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

    browser_payload = payload.model_copy(update={"source": "browser"})
    saved = await _save_listing(browser_payload, allow_existing=True)

    async with SessionLocal() as session:
        result = await session.scalar(
            select(SearchResult).where(SearchResult.url == str(browser_payload.source_url))
        )
        if result is not None:
            result.import_status = "imported"
            result.imported_listing_id = saved.id
            result.import_error = None
            result.imported_at = datetime.now(UTC)
            await session.commit()
    return saved


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


@app.post("/api/v1/search/run")
async def run_search_now() -> dict[str, object]:
    summary = await run_search()
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
async def search_results(limit: int = 50, only_new: bool = False) -> list[dict[str, object]]:
    safe_limit = min(max(limit, 1), 200)
    async with SessionLocal() as session:
        query = select(SearchResult).order_by(SearchResult.first_seen.desc()).limit(safe_limit)
        if only_new:
            query = query.where(SearchResult.status.like("new:%"))
        rows = (await session.scalars(query)).all()

    result: list[dict[str, object]] = []
    for row in rows:
        analysis = analyze_search_result(
            row.title or "",
            row.snippet,
            max_price=settings.max_price_kzt,
            min_area=settings.min_area_m2,
            max_area=settings.max_area_m2,
        )
        result.append(
            {
                "id": row.id,
                "url": row.url,
                "search_engine": row.search_engine,
                "query": row.query,
                "title": row.title,
                "snippet": row.snippet,
                "status": row.status.split(":", 1)[0],
                "priority": row.status.split(":", 1)[1] if ":" in row.status else "normal",
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
