"""Tests for kayak.cli.init_db seed and sync helpers."""

from unittest import mock

from kayak.db.models import FetchUrl, State


def test_seed_states_adds_records(session):
    """_seed_states inserts the expected State records."""
    from kayak.cli.init_db import _seed_states

    _seed_states(session)
    session.flush()

    states = session.query(State).all()
    assert len(states) > 0
    names = {s.name for s in states}
    assert "ID" in names
    assert "OR" in names


def test_seed_states_idempotent(session):
    """Calling _seed_states twice does not duplicate records."""
    from kayak.cli.init_db import _seed_states

    _seed_states(session)
    session.flush()
    count1 = session.query(State).count()

    _seed_states(session)
    session.flush()
    count2 = session.query(State).count()

    assert count1 == count2


def test_sync_sources_adds_records(session):
    """_sync_sources creates FetchUrl records from YAML source list."""
    from kayak.cli.init_db import _sync_sources

    fake_sources = [
        {"url": "https://example.com/usgs1", "parser": "usgs", "hours": ""},
        {"url": "https://example.com/noaa1", "parser": "noaa", "hours": "6,12"},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        count = _sync_sources(session)
    session.flush()

    assert count == 2
    assert session.query(FetchUrl).count() == 2


def test_sync_sources_updates_existing(session):
    """_sync_sources updates parser/hours on an existing FetchUrl row."""
    from kayak.cli.init_db import _sync_sources

    # First sync
    sources_v1 = [
        {"url": "https://example.com/src", "parser": "usgs", "hours": ""},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=sources_v1):
        _sync_sources(session)
    session.flush()

    # Second sync with updated parser
    sources_v2 = [
        {"url": "https://example.com/src", "parser": "noaa", "hours": "0,12"},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=sources_v2):
        count = _sync_sources(session)
    session.flush()

    assert count == 0  # no new records
    fu = session.query(FetchUrl).filter_by(url="https://example.com/src").one()
    assert fu.parser == "noaa"
    assert fu.hours == "0,12"


def test_sync_sources_returns_new_count(session):
    """_sync_sources returns only the count of newly inserted records."""
    from kayak.cli.init_db import _sync_sources

    # Pre-populate one record
    session.add(FetchUrl(url="https://example.com/pre", parser="usgs", is_active=True))
    session.flush()

    fake_sources = [
        {"url": "https://example.com/pre", "parser": "usgs", "hours": ""},
        {"url": "https://example.com/new", "parser": "noaa", "hours": ""},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        count = _sync_sources(session)

    assert count == 1  # only the new URL
