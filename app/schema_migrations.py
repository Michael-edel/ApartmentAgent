from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

# create_all intentionally remains the first step; these definitions only cover
# columns introduced after the initial MVP schema was deployed.
_COLUMNS: dict[str, dict[str, str]] = {
    "listings": {
        "description": "TEXT",
        "photo_urls": "JSON NOT NULL DEFAULT '[]'",
        "ai_analysis": "JSON",
        "ai_analyzed_at": "TIMESTAMP WITH TIME ZONE",
        "last_imported_at": "TIMESTAMP WITH TIME ZONE",
    },
    "price_snapshots": {"price_per_m2": "BIGINT"},
    "search_results": {
        "import_status": "VARCHAR(30) DEFAULT 'pending'",
        "imported_listing_id": "INTEGER REFERENCES listings(id) ON DELETE SET NULL",
        "import_error": "TEXT",
        "imported_at": "TIMESTAMP WITH TIME ZONE",
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
