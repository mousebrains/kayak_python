"""SQLAlchemy engine and session factory."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from kayak.config import DATABASE_URL

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _set_sqlite_pragma(dbapi_conn: object, _connection_record: object) -> None:
    """Set SQLite PRAGMAs on each new connection."""
    cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def get_engine(url: str | None = None) -> Engine:
    """Return the singleton engine, creating it on first call.

    When ``url`` is supplied, the prior engine (if any) is disposed before the
    new one is built — otherwise every ``get_engine(url=…)`` call would orphan
    its connection pool. The cached session factory is also invalidated so the
    next ``get_session_factory()`` call binds to the new engine instead of the
    disposed one.
    """
    global _engine, _session_factory
    if _engine is None or url is not None:
        if _engine is not None and url is not None:
            _engine.dispose()
            _session_factory = None
        db_url = url or DATABASE_URL
        connect_args = {}
        if db_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(db_url, connect_args=connect_args, echo=False)
        if db_url.startswith("sqlite"):
            event.listen(_engine, "connect", _set_sqlite_pragma)
    return _engine


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    """Return a sessionmaker bound to the current engine.

    Invariant: the returned factory is always bound to the engine that
    ``get_engine()`` would currently return — never to a disposed engine.
    """
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
