from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from knowledge.core.settings import get_settings


settings = get_settings()

is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {}
engine_kwargs = {"future": True}
if is_sqlite:
    connect_args = {
        "check_same_thread": False,
        "timeout": max(1.0, settings.sqlite_busy_timeout_ms / 1000),
    }
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


if is_sqlite:
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"PRAGMA busy_timeout = {int(settings.sqlite_busy_timeout_ms)}")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
