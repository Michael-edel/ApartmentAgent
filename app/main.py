import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.checker import check_all_listings, periodic_checker
from app.config import get_settings
from app.database import SessionLocal, engine
from app.importer import ListingImportError, import_krisha_listing
from app.models import Base, Listing, ListingCheck, PriceSnapshot
from app.scoring import assess_listing
from app.schemas import ListingCreate, ListingImportRequest, ListingResponse

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    stop_event = asyncio.Event()
    checker_task = asyncio.create_task(periodic_checker(stop_event))
    yield
    stop_event.set()
    await checker_task
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.5.0", lifespan=lifespan)
_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
async def web_app() -> FileResponse:
    return FileResponse(_static_dir / "index.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> FileResponse:
    return FileResponse(
        _static_dir / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/service-worker.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse(
        _static_dir / "service-worker.js",
        media_type="application/javascript",
    )


@app.get("/health")
async def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "storage": "postgresql",
        "automatic_check_minutes": max(settings.check_interval_minutes, 15),
    }


def _to_response(row: Listing) -> ListingResponse:
    payload = ListingCreate(
        source=row.source,
        source_url=row.source_url,
        title=row.title,
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
    )
    return ListingResponse(
        id=row.id,
        created_at=row.created_at,
        assessment=assess_listing(payload, settings),
        **payload.model_dump(),
    )


async def _save_listing(payload: ListingCreate) -> ListingResponse:
    values = payload.model_dump(mode="json")
    values["source_url"] = str(payload.source_url)
    row = Listing(**values)

    async with SessionLocal() as session:
        session.add(row)
        try:
            await session.flush()
            session.add(PriceSnapshot(listing_id=row.id, price_kzt=row.price_kzt))
            await session.commit()
            await session.refresh(row)
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Listing with this URL already exists",
            ) from exc
    return _to_response(row)


@app.post(
    "/api/v1/listings",
    response_model=ListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_listing(payload: ListingCreate) -> ListingResponse:
    return await _save_listing(payload)


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
    return await _save_listing(listing_data)


@app.post("/api/v1/checks/run")
async def run_checks_now() -> dict[str, int]:
    summary = await check_all_listings()
    return {
        "checked": summary.checked,
        "updated": summary.updated,
        "blocked": summary.blocked,
        "errors": summary.errors,
    }


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


@app.get("/api/v1/listings", response_model=list[ListingResponse])
async def list_listings(min_score: int | None = None) -> list[ListingResponse]:
    async with SessionLocal() as session:
        rows = (await session.scalars(select(Listing).order_by(Listing.created_at.desc()))).all()
    items = [_to_response(row) for row in rows]
    if min_score is not None:
        items = [item for item in items if item.assessment.score >= min_score]
    return items


@app.get("/api/v1/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(listing_id: int) -> ListingResponse:
    async with SessionLocal() as session:
        row = await session.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    return _to_response(row)
