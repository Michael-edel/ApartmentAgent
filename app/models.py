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
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    source_url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str] = mapped_column(String(100), default="Астана")
    district: Mapped[str | None] = mapped_column(String(150), nullable=True)
    residential_complex: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_kzt: Mapped[int] = mapped_column(BigInteger)
    area_m2: Mapped[float] = mapped_column(Float)
    rooms: Mapped[int] = mapped_column(Integer)
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    floors_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_full_two_room: Mapped[bool] = mapped_column(Boolean, default=True)
    mortgage_supported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
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
