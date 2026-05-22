"""Tests for kayak.analytics.geoip.

Drives the cache-load path, the mmdb record-unpacking path, and the
public lookup / lookup_name / lookup_subdivision helpers. The mmdb
reader is stubbed so we don't depend on having a DB-IP file on disk.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from kayak.analytics import geoip


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each test gets its own cache file + reader so they don't poison each other."""
    monkeypatch.setattr(geoip, "_LOOKUP_CACHE_PATH", tmp_path / "lookup_cache.json")
    monkeypatch.setattr(geoip, "DEFAULT_DB_DIR", tmp_path / "geoip")
    geoip.reset_cache_for_tests()


class _FakeReader:
    """Mimics maxminddb.Reader.get() for the records we feed our tests."""

    def __init__(self, records: dict[str, dict | None]) -> None:
        self._records = records

    def get(self, ip: str):  # type: ignore[no-untyped-def]
        return self._records.get(ip)

    def close(self) -> None:  # pragma: no cover — driven via reset_cache_for_tests
        pass


def _install_reader(monkeypatch: pytest.MonkeyPatch, records: dict[str, dict | None]) -> None:
    monkeypatch.setattr(geoip, "_open_reader", lambda _db_dir: _FakeReader(records))


def test_lookup_returns_country_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_reader(
        monkeypatch,
        {
            "1.1.1.1": {
                "country": {"iso_code": "AU", "names": {"en": "Australia"}},
                "subdivisions": [{"names": {"en": "New South Wales"}}],
            }
        },
    )
    assert geoip.lookup("1.1.1.1") == "AU"
    assert geoip.lookup_name("1.1.1.1") == "Australia"
    assert geoip.lookup_subdivision("1.1.1.1") == "New South Wales"


def test_lookup_handles_missing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_reader(monkeypatch, {})  # nothing for the queried IP
    assert geoip.lookup("203.0.113.99") == "-"
    assert geoip.lookup_name("203.0.113.99") == ""
    assert geoip.lookup_subdivision("203.0.113.99") == ""


def test_lookup_handles_record_without_subdivisions(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_reader(
        monkeypatch,
        {"5.6.7.8": {"country": {"iso_code": "DE", "names": {"en": "Germany"}}}},
    )
    assert geoip.lookup("5.6.7.8") == "DE"
    assert geoip.lookup_name("5.6.7.8") == "Germany"
    assert geoip.lookup_subdivision("5.6.7.8") == ""  # not present in record


def test_lookup_reader_disabled_returns_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoip, "_open_reader", lambda _db_dir: None)
    assert geoip.lookup("1.2.3.4") == "-"
    assert geoip.lookup_name("1.2.3.4") == ""
    assert geoip.lookup_subdivision("1.2.3.4") == ""


def test_cache_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A lookup populates the cache and flush_cache writes the new shape."""
    _install_reader(
        monkeypatch,
        {
            "8.8.8.8": {
                "country": {"iso_code": "US", "names": {"en": "United States"}},
                "subdivisions": [{"names": {"en": "California"}}],
            }
        },
    )
    assert geoip.lookup("8.8.8.8") == "US"
    assert geoip.lookup_name("8.8.8.8") == "United States"
    geoip.flush_cache()

    cache_path = tmp_path / "lookup_cache.json"
    assert cache_path.exists()
    saved = json.loads(cache_path.read_text())
    assert "8.8.8.8" in saved
    code, name, sub, ts = saved["8.8.8.8"]
    assert code == "US"
    assert name == "United States"
    assert sub == "California"
    assert ts > 0


def test_cache_load_evicts_stale_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An entry older than the TTL is dropped at load time."""
    cache_path = tmp_path / "lookup_cache.json"
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    stale_ts = now_ts - (geoip._LOOKUP_CACHE_TTL_DAYS + 1) * 86400
    fresh_ts = now_ts - 60  # one minute ago
    cache_path.write_text(
        json.dumps(
            {
                "1.1.1.1": ["AU", "Australia", "New South Wales", fresh_ts],
                "9.9.9.9": ["JP", "Japan", "Tokyo", stale_ts],  # too old
            }
        )
    )
    # Reader is irrelevant for already-cached entries.
    monkeypatch.setattr(geoip, "_open_reader", lambda _db_dir: None)
    assert geoip.lookup("1.1.1.1") == "AU"
    # 9.9.9.9 was evicted; with no reader, it resolves to "-".
    assert geoip.lookup("9.9.9.9") == "-"


def test_cache_load_ignores_legacy_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Old `[code, ts]` entries from pre-City-Lite are discarded on load."""
    cache_path = tmp_path / "lookup_cache.json"
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    cache_path.write_text(
        json.dumps(
            {
                "1.1.1.1": ["AU", now_ts],  # legacy 2-tuple shape
                "8.8.8.8": ["US", "United States", "California", now_ts],
            }
        )
    )
    monkeypatch.setattr(geoip, "_open_reader", lambda _db_dir: None)
    # 1.1.1.1 had legacy shape → discarded → no reader → "-"
    assert geoip.lookup("1.1.1.1") == "-"
    # 8.8.8.8 had current shape → kept
    assert geoip.lookup("8.8.8.8") == "US"


def test_cache_load_tolerates_corrupt_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "lookup_cache.json").write_text("not json")
    _install_reader(monkeypatch, {})
    # Just doesn't crash.
    assert geoip.lookup("1.1.1.1") == "-"


def test_current_db_path_uses_year_month(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = geoip._current_db_path(tmp_path)
    assert p.parent == tmp_path
    # name has the year-month tail
    assert p.name.startswith("dbip-city-lite-")
    assert p.name.endswith(".mmdb")
