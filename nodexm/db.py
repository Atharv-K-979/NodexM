from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import event, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

_connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


# Columns added after the first release. create_all() only creates missing tables, so existing
# databases get these columns here. Each entry: table -> [(column, SQL type, index SQL or None)].
_ADDED_COLUMNS = {
    "users": [
        ("google_sub", "VARCHAR(255)", "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub)"),
        ("avatar_url", "VARCHAR(1024)", None),
    ],
    "sources": [("origin", "VARCHAR(64)", None)],
    "claims": [("independence", "JSON", None)],
    "ledger_claims": [
        ("pending_status", "VARCHAR(16)", None),
        ("pending_runs", "INTEGER NOT NULL DEFAULT 0", None),
        ("confirmed", "BOOLEAN NOT NULL DEFAULT 1", None),
        ("evidence", "JSON", None),
        ("source_keys", "JSON", None),
    ],
    "change_events": [
        ("confirmation", "VARCHAR(24) NOT NULL DEFAULT ''", None),
        ("evidence_before", "JSON", None),
        ("evidence_after", "JSON", None),
        ("feedback", "VARCHAR(8)", None),
        ("feedback_at", "DATETIME", None),
    ],
}


def _add_missing_columns() -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, sql_type, index_sql in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                    if index_sql:
                        conn.execute(text(index_sql))


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
