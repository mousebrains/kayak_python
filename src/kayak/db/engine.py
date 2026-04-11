"""SQLAlchemy engine and session factory (replaces MyDB.C)."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from kayak.config import DATABASE_URL

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(url: str | None = None) -> Engine:
    """Return the singleton engine, creating it on first call."""
    global _engine
    if _engine is None or url is not None:
        db_url = url or DATABASE_URL
        connect_args = {}
        if db_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(db_url, connect_args=connect_args, echo=False)
        # Enable WAL mode and foreign keys for SQLite
        if db_url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn: object, _connection_record: object) -> None:
                cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

    return _engine


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Return a sessionmaker bound to the engine."""
    global _session_factory
    if _session_factory is None or url is not None:
        _session_factory = sessionmaker(bind=get_engine(url))
    return _session_factory


def get_session(url: str | None = None) -> Session:
    """Create and return a new session."""
    return get_session_factory(url)()


def reset() -> None:
    """Reset engine and session factory (for testing)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
