"""Shared test fixtures using in-memory SQLite."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from kayak.db.models import Base


@pytest.fixture()
def engine():
    """Create a fresh in-memory SQLite engine per test."""
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    """Provide a transactional session that rolls back after the test."""
    connection = engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection)

    yield sess

    sess.close()
    transaction.rollback()
    connection.close()
