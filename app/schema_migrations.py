from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

# create_all intentionally remains the first step; these definitions only cover
# columns introduced after the initial MVP schema was deployed.
_COLUMNS: dict[str, dict[str, str]] = {
    "search_preferences": {"city": "VARCHAR(30)", "market_type": "VARCHAR(20)", "min_price_kzt": "BIGINT", "max_price_kzt": "BIGINT", "min_area_m2": "DOUBLE PRECISION", "max_area_m2": "DOUBLE PRECISION", "min_bathrooms": "INTEGER", "exclude_pledged": "BOOLEAN", "active_profile_id": "INTEGER", "conditions": "JSON"},
    "listings": {
        "market_type": "VARCHAR(20)",
        "complex_description": "TEXT",
        "complex_description_source_url": "TEXT",
        "complex_description_source_kind": "VARCHAR(30)",
        "complex_description_observed_at": "TIMESTAMP WITH TIME ZONE",
        "krisha_district_price_per_m2": "BIGINT",
        "krisha_city_price_per_m2": "BIGINT",
        "krisha_comparison_observed_at": "TIMESTAMP WITH TIME ZONE",
        "krisha_comparison_source": "VARCHAR(50)",
        "krisha_comparison_district": "VARCHAR(150)",
        "bathrooms_count": "INTEGER",
        "bathrooms_count_is_lower_bound": "BOOLEAN NOT NULL DEFAULT FALSE",
        "description": "TEXT",
        "repair_condition": "VARCHAR(500)",
        "repair_source_kind": "VARCHAR(20)",
        "repair_evidence": "VARCHAR(500)",
        "mortgage_source_kind": "VARCHAR(20)",
        "mortgage_evidence": "VARCHAR(500)",
        "is_pledged": "BOOLEAN",
        "property_details": "JSON NOT NULL DEFAULT '{}'",
        "details_imported_at": "TIMESTAMP WITH TIME ZONE",
        "seller_name": "VARCHAR(200)",
        "seller_type": "VARCHAR(100)",
        "address": "VARCHAR(500)",
        "latitude": "DOUBLE PRECISION",
        "longitude": "DOUBLE PRECISION",
        "photo_urls": "JSON NOT NULL DEFAULT '[]'",
        "twogis_name": "VARCHAR(300)",
        "twogis_rating": "DOUBLE PRECISION",
        "twogis_review_count": "INTEGER",
        "twogis_url": "TEXT",
        "twogis_checked_at": "TIMESTAMP WITH TIME ZONE",
        "ai_analysis": "JSON",
        "ai_analyzed_at": "TIMESTAMP WITH TIME ZONE",
        "last_imported_at": "TIMESTAMP WITH TIME ZONE",
        "sold_at": "TIMESTAMP WITH TIME ZONE",
        "source_archived_at": "TIMESTAMP WITH TIME ZONE",
        "source_unavailable_at": "TIMESTAMP WITH TIME ZONE",
        "source_availability_observed_at": "TIMESTAMP WITH TIME ZONE",
    },
    "price_snapshots": {"price_per_m2": "BIGINT"},
    "search_history": {"city": "VARCHAR(30)"},
    "search_results": {
        "city": "VARCHAR(30)",
        "market_type": "VARCHAR(20)",
        "seller_type": "VARCHAR(100)",
        "mortgage_supported": "BOOLEAN",
        "is_pledged": "BOOLEAN",
        "bathrooms_count": "INTEGER",
        "bathrooms_count_is_lower_bound": "BOOLEAN NOT NULL DEFAULT FALSE",
        "import_status": "VARCHAR(30) DEFAULT 'pending'",
        "imported_listing_id": "INTEGER REFERENCES listings(id) ON DELETE SET NULL",
        "import_error": "TEXT",
        "imported_at": "TIMESTAMP WITH TIME ZONE",
        "source_archived_at": "TIMESTAMP WITH TIME ZONE",
        "source_unavailable_at": "TIMESTAMP WITH TIME ZONE",
        "source_availability_observed_at": "TIMESTAMP WITH TIME ZONE",
    },
}


def ensure_legacy_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    for table_name, columns in _COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, definition in columns.items():
            if column_name in existing:
                continue
            connection.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
            )
        if table_name == "search_results":
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_search_results_import_status "
                    "ON search_results (import_status)"
                )
            )
    connection.execute(text(
        "UPDATE search_results SET mortgage_supported = "
        "(SELECT mortgage_supported FROM listings WHERE listings.id = search_results.imported_listing_id) "
        "WHERE mortgage_supported IS NULL AND imported_listing_id IS NOT NULL"
    ))
