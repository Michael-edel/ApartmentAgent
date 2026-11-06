from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SearchPreference(Base):
    __tablename__ = "search_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rooms: Mapped[int] = mapped_column(Integer, default=2)
    max_price_kzt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    min_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_bathrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    source_url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    repair_condition: Mapped[str | None] = mapped_column(String(500), nullable=True)
    repair_source_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    repair_evidence: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mortgage_source_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mortgage_evidence: Mapped[str | None] = mapped_column(String(500), nullable=True)
    property_details: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    details_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    city: Mapped[str] = mapped_column(String(100), default="Астана")
    district: Mapped[str | None] = mapped_column(String(150), nullable=True)
    residential_complex: Mapped[str | None] = mapped_column(String(200), nullable=True)
    complex_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    complex_description_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    complex_description_source_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    complex_description_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_kzt: Mapped[int] = mapped_column(BigInteger)
    area_m2: Mapped[float] = mapped_column(Float)
    rooms: Mapped[int] = mapped_column(Integer)
    bathrooms_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms_count_is_lower_bound: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floors_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seller_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    twogis_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    twogis_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    twogis_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    twogis_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    twogis_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_full_two_room: Mapped[bool] = mapped_column(Boolean, default=True)
    mortgage_supported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    krisha_district_price_per_m2: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    krisha_city_price_per_m2: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    krisha_comparison_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    krisha_comparison_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    krisha_comparison_district: Mapped[str | None] = mapped_column(String(150), nullable=True)
    photo_urls: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    ai_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ai_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["PriceSnapshot"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", lazy="selectin"
    )
    checks: Mapped[list["ListingCheck"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", lazy="selectin"
    )


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    price_kzt: Mapped[int] = mapped_column(BigInteger)
    price_per_m2: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    listing: Mapped[Listing] = relationship(back_populates="prices")


class ListingCheck(Base):
    __tablename__ = "listing_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_price_kzt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_price_kzt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    listing: Mapped[Listing] = relationship(back_populates="checks")


class SearchResult(Base):
    __tablename__ = "search_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    search_engine: Mapped[str] = mapped_column(String(30), index=True)
    query: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(500))
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    mortgage_supported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bathrooms_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms_count_is_lower_bound: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    import_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    imported_listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    import_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_engine: Mapped[str] = mapped_column(String(30), index=True)
    query: Mapped[str] = mapped_column(Text)
    searched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total_found: Mapped[int] = mapped_column(Integer, default=0)
    new_found: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="ok", index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class TelegramNotification(Base):
    __tablename__ = "telegram_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BathroomCheckJob(Base):
    __tablename__ = "bathroom_check_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_claim_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BathroomCheckTask(Base):
    __tablename__ = "bathroom_check_tasks"
    __table_args__ = (UniqueConstraint("job_id", "url", name="uq_bathroom_check_job_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("bathroom_check_jobs.id", ondelete="CASCADE"), index=True)
    search_result_id: Mapped[int | None] = mapped_column(ForeignKey("search_results.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BathroomCheckControl(Base):
    __tablename__ = "bathroom_check_control"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_job_id: Mapped[int | None] = mapped_column(ForeignKey("bathroom_check_jobs.id", ondelete="SET NULL"), nullable=True)
