from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def initialize_database() -> None:
    """Create new tables and add columns to databases created by older releases."""
    from app.models import Base
    from app.schema_migrations import ensure_legacy_columns

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(ensure_legacy_columns)

    # Enrich only descriptions already stored locally; this never requests a site.
    from app.listing_service import (
        backfill_author_types, backfill_mortgage_and_addresses, backfill_pledge_status, backfill_repair_descriptions,
    )
    await backfill_repair_descriptions()
    await backfill_mortgage_and_addresses()
    await backfill_pledge_status()
    await backfill_author_types()
