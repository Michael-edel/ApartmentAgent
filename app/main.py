from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.importer import ListingImportError, import_krisha_listing
from app.scoring import assess_listing
from app.schemas import ListingCreate, ListingImportRequest, ListingResponse

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.3.0")

_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

_listings: list[ListingResponse] = []


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
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


def _save_listing(payload: ListingCreate) -> ListingResponse:
    normalized_url = str(payload.source_url)
    if any(str(item.source_url) == normalized_url for item in _listings):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Listing with this URL already exists",
        )

    listing = ListingResponse(
        id=len(_listings) + 1,
        created_at=datetime.now(UTC),
        assessment=assess_listing(payload, settings),
        **payload.model_dump(),
    )
    _listings.append(listing)
    return listing


@app.post(
    "/api/v1/listings",
    response_model=ListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_listing(payload: ListingCreate) -> ListingResponse:
    return _save_listing(payload)


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
    return _save_listing(listing_data)


@app.get("/api/v1/listings", response_model=list[ListingResponse])
async def list_listings(min_score: int | None = None) -> list[ListingResponse]:
    if min_score is None:
        return _listings
    return [item for item in _listings if item.assessment.score >= min_score]


@app.get("/api/v1/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(listing_id: int) -> ListingResponse:
    for item in _listings:
        if item.id == listing_id:
            return item
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
