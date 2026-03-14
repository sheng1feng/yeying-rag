from __future__ import annotations

from sqlalchemy import Engine, inspect, text


TASK_COLUMNS: dict[str, str] = {
    "claimed_by": "VARCHAR(128)",
    "claimed_at": "DATETIME",
    "heartbeat_at": "DATETIME",
    "attempt": "INTEGER",
    "last_stage": "VARCHAR(64)",
}

TASK_ITEM_COLUMNS: dict[str, str] = {
    "stage": "VARCHAR(64)",
    "duration_ms": "INTEGER",
    "error_type": "VARCHAR(128)",
}

RUNTIME_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("import_tasks", "ix_import_tasks_status_created_at", "status, created_at"),
    ("import_tasks", "ix_import_tasks_owner_status_created_at", "owner_wallet_address, status, created_at"),
)


def ensure_runtime_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as connection:
        _ensure_columns(connection, inspector, "import_tasks", TASK_COLUMNS)
        _ensure_columns(connection, inspector, "import_task_items", TASK_ITEM_COLUMNS)
        inspector = inspect(connection)
        _ensure_indexes(connection, inspector)


def _ensure_columns(connection, inspector, table_name: str, columns: dict[str, str]) -> None:
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    for column_name, column_type in columns.items():
        if column_name in existing:
            continue
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def _ensure_indexes(connection, inspector) -> None:
    existing = {index["name"] for _, index in _iter_indexes(inspector)}
    for table_name, index_name, columns_sql in RUNTIME_INDEXES:
        if index_name in existing:
            continue
        connection.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({columns_sql})"))


def _iter_indexes(inspector):
    for table_name in ("import_tasks", "import_task_items"):
        for index in inspector.get_indexes(table_name):
            yield table_name, index
