"""Tests for kayak.cli.init_db seed and sync helpers."""

from argparse import Namespace
from unittest import mock
from unittest.mock import patch

import pytest
from sqlalchemy import text

from kayak.db.models import FetchUrl, Source, State


def test_seed_states_adds_records(session):
    """_seed_states inserts the expected State records."""
    from kayak.cli.init_db import _seed_states

    _seed_states(session)
    session.flush()

    states = session.query(State).all()
    assert len(states) > 0
    names = {s.name for s in states}
    assert "Idaho" in names
    assert "Oregon" in names


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
    """sync_sources creates FetchUrl records from YAML source list."""
    from kayak.cli.init_db import sync_sources

    fake_sources = [
        {"url": "https://example.com/usgs1", "parser": "nwps", "hours": ""},
        {"url": "https://example.com/noaa1", "parser": "noaa", "hours": "6,12"},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        count = sync_sources(session)
    session.flush()

    assert count == 2
    assert session.query(FetchUrl).count() == 2


def test_sync_sources_updates_existing(session):
    """sync_sources updates parser/hours on an existing FetchUrl row."""
    from kayak.cli.init_db import sync_sources

    # First sync
    sources_v1 = [
        {"url": "https://example.com/src", "parser": "nwps", "hours": ""},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=sources_v1):
        sync_sources(session)
    session.flush()

    # Second sync with updated parser
    sources_v2 = [
        {"url": "https://example.com/src", "parser": "noaa", "hours": "0,12"},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=sources_v2):
        count = sync_sources(session)
    session.flush()

    assert count == 0  # no new records
    fu = session.query(FetchUrl).filter_by(url="https://example.com/src").one()
    assert fu.parser == "noaa"
    assert fu.hours == "0,12"


def test_sync_sources_returns_new_count(session):
    """sync_sources returns only the count of newly inserted records."""
    from kayak.cli.init_db import sync_sources

    # Pre-populate one record
    session.add(FetchUrl(url="https://example.com/pre", parser="nwps", is_active=True))
    session.flush()

    fake_sources = [
        {"url": "https://example.com/pre", "parser": "nwps", "hours": ""},
        {"url": "https://example.com/new", "parser": "noaa", "hours": ""},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        count = sync_sources(session)

    assert count == 1  # only the new URL


def test_sync_sources_creates_sources_with_timezone(session):
    """A stations: block upserts Source rows with timezone set."""
    from kayak.cli.init_db import sync_sources

    fake_sources = [
        {
            "url": "https://example.com/usbr",
            "parser": "usbr",
            "hours": "",
            "stations": {
                "BENO": "America/Los_Angeles",
                "CSCI": "America/Boise",
            },
        }
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        sync_sources(session)
    session.flush()

    sources = {s.name: s for s in session.query(Source).all()}
    assert set(sources) == {"BENO", "CSCI"}
    assert sources["BENO"].timezone == "America/Los_Angeles"
    assert sources["CSCI"].timezone == "America/Boise"
    assert sources["BENO"].agency == "usbr"


def test_sync_sources_updates_timezone_on_existing_source(session):
    """Changing the YAML TZ updates the existing Source row."""
    from kayak.cli.init_db import sync_sources

    # Pre-populate with wrong TZ
    fu = FetchUrl(url="https://example.com/x", parser="usbr", is_active=True)
    session.add(fu)
    session.flush()
    session.add(Source(name="STN", fetch_url_id=fu.id, timezone="America/Los_Angeles"))
    session.flush()

    fake_sources = [
        {
            "url": "https://example.com/x",
            "parser": "usbr",
            "hours": "",
            "stations": {"STN": "America/Boise"},
        }
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        sync_sources(session)
    session.flush()

    stn = session.query(Source).filter_by(name="STN").one()
    assert stn.timezone == "America/Boise"


def test_sync_sources_rejects_invalid_timezone(session):
    """A bogus IANA TZ in stations: raises ValueError at sync time."""
    from kayak.cli.init_db import sync_sources

    fake_sources = [
        {
            "url": "https://example.com/bad",
            "parser": "usbr",
            "hours": "",
            "stations": {"STN": "Not/A_Real_Zone"},
        }
    ]
    with (
        mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources),
        pytest.raises(ValueError, match="Not/A_Real_Zone"),
    ):
        sync_sources(session)


def test_sync_sources_missing_stations_key_works(session):
    """Entries without a stations: block still sync normally."""
    from kayak.cli.init_db import sync_sources

    fake_sources = [
        {"url": "https://example.com/nostations", "parser": "nwps", "hours": ""},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        count = sync_sources(session)
    session.flush()

    assert count == 1
    assert session.query(Source).count() == 0


def test_sync_sources_deactivates_missing_urls(session):
    """FetchUrl rows whose url is no longer in the YAML get is_active=False.

    Retirement mechanism: the row stays (observations still reference it via
    source.fetch_url_id), but fetch skips inactive rows.
    """
    from kayak.cli.init_db import sync_sources

    # Seed a stale active FetchUrl not in the YAML, plus one the YAML covers.
    session.add(FetchUrl(url="https://example.com/retired", parser="nwps", is_active=True))
    session.add(FetchUrl(url="https://example.com/kept", parser="nwps", is_active=True))
    session.flush()

    fake_sources = [
        {"url": "https://example.com/kept", "parser": "nwps", "hours": ""},
        {"url": "https://example.com/fresh", "parser": "nwps", "hours": ""},
    ]
    with mock.patch("kayak.cli.init_db.load_sources", return_value=fake_sources):
        sync_sources(session)
    session.flush()

    retired = session.query(FetchUrl).filter_by(url="https://example.com/retired").one()
    kept = session.query(FetchUrl).filter_by(url="https://example.com/kept").one()
    fresh = session.query(FetchUrl).filter_by(url="https://example.com/fresh").one()
    assert retired.is_active is False, "URL removed from YAML should deactivate"
    assert kept.is_active is True, "URL present in YAML should stay active"
    assert fresh.is_active is True, "new URL is inserted with is_active=True"


def test_init_db_skips_stamping_on_existing_db(engine, capsys):
    """init-db on a DB that already tracks any migration must not
    blanket-stamp the rest. The prior behavior would silently mark
    unapplied migrations as applied, making `levels migrate` skip them.
    """
    from kayak.cli.init_db import init_db
    from kayak.cli.migrate import _ensure_tracking_table, stamp

    with patch("kayak.cli.migrate.get_engine", return_value=engine):
        _ensure_tracking_table()
        stamp("0001")

    args = Namespace(drop=False, no_seed=True)
    with (
        patch("kayak.cli.init_db.get_engine", return_value=engine),
        patch("kayak.cli.migrate.get_engine", return_value=engine),
    ):
        init_db(args)

    with engine.connect() as conn:
        versions = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations")).all()}
    assert versions == {"0001"}, "init-db must not stamp anything beyond the existing 0001"
    assert "already tracks" in capsys.readouterr().out
