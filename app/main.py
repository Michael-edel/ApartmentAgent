from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, status

from app.config import get_settings
from app.scoring import assess_listing
from app.schemas import ListingCreate, ListingResponse

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")

_listings: list[ListingResponse] = []


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.post(
    "/api/v1/listings",
    response_model=ListingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_listing(payload: ListingCreate) -> ListingResponse:
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
