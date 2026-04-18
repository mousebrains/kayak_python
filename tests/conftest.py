"""Shared test fixtures using in-memory SQLite."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from kayak.db.models import (
    Base,
    FetchUrl,
    Gauge,
    GaugeSource,
    Reach,
    Source,
)


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
    sess = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield sess

    sess.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def sample_source(session) -> Source:
    """Create a Source with a FetchUrl for testing."""
    fetch_url = FetchUrl(url="https://example.com/data", parser="usgs", is_active=True)
    session.add(fetch_url)
    session.flush()

    source = Source(name="test_source", agency="USGS", fetch_url_id=fetch_url.id)
    session.add(source)
    session.flush()
    return source


@pytest.fixture()
def sample_gauge(session) -> Gauge:
    """Create a Gauge for testing."""
    gauge = Gauge(name="test_gauge", usgs_id="12345678")
    session.add(gauge)
    session.flush()
    return gauge


@pytest.fixture()
def sample_reach(session, sample_gauge) -> Reach:
    """Create a Reach with a Gauge for testing."""
    reach = Reach(
        name="test_reach",
        display_name="Test River - Upper",
        sort_name="Test River",
        gauge_id=sample_gauge.id,
    )
    session.add(reach)
    session.flush()
    return reach


@pytest.fixture()
def linked_source_gauge(session, sample_source, sample_gauge) -> tuple[Source, Gauge]:
    """Create a Source linked to a Gauge via gauge_source."""
    session.add(GaugeSource(gauge_id=sample_gauge.id, source_id=sample_source.id))
    session.flush()
    return sample_source, sample_gauge
