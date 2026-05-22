"""Tests for kayak.analytics.geoip.

Drives the cache-load path, the two mmdb record-unpacking paths
(country and ASN), and the public lookup helpers. Both readers are
stubbed so we don't depend on having DB-IP files on disk.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from kayak.analytics import geoip


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each test gets its own cache file + readers so they don't poison each other."""
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


def _install_city(monkeypatch: pytest.MonkeyPatch, records: dict[str, dict | None]) -> None:
    monkeypatch.setattr(geoip, "_open_city_reader", lambda _db_dir: _FakeReader(records))


def _install_asn(monkeypatch: pytest.MonkeyPatch, records: dict[str, dict | None]) -> None:
    monkeypatch.setattr(geoip, "_open_asn_reader", lambda _db_dir: _FakeReader(records))


def test_lookup_returns_country_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_city(
        monkeypatch,
        {
            "1.1.1.1": {
                "country": {"iso_code": "AU", "names": {"en": "Australia"}},
                "subdivisions": [{"names": {"en": "New South Wales"}}],
            }
        },
    )
    _install_asn(monkeypatch, {})
    assert geoip.lookup("1.1.1.1") == "AU"
    assert geoip.lookup_name("1.1.1.1") == "Australia"
    assert geoip.lookup_subdivision("1.1.1.1") == "New South Wales"


def test_lookup_handles_missing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_city(monkeypatch, {})
    _install_asn(monkeypatch, {})
    assert geoip.lookup("203.0.113.99") == "-"
    assert geoip.lookup_name("203.0.113.99") == ""
    assert geoip.lookup_subdivision("203.0.113.99") == ""


def test_lookup_handles_record_without_subdivisions(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_city(
        monkeypatch,
        {"5.6.7.8": {"country": {"iso_code": "DE", "names": {"en": "Germany"}}}},
    )
    _install_asn(monkeypatch, {})
    assert geoip.lookup("5.6.7.8") == "DE"
    assert geoip.lookup_name("5.6.7.8") == "Germany"
    assert geoip.lookup_subdivision("5.6.7.8") == ""  # not present in record


def test_lookup_reader_disabled_returns_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoip, "_open_city_reader", lambda _db_dir: None)
    monkeypatch.setattr(geoip, "_open_asn_reader", lambda _db_dir: None)
    assert geoip.lookup("1.2.3.4") == "-"
    assert geoip.lookup_name("1.2.3.4") == ""
    assert geoip.lookup_subdivision("1.2.3.4") == ""
    assert geoip.lookup_asn("1.2.3.4") == 0
    assert geoip.lookup_asn_org("1.2.3.4") == ""


def test_lookup_asn_returns_number_and_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_city(monkeypatch, {})
    _install_asn(
        monkeypatch,
        {
            "47.82.10.78": {
                "autonomous_system_number": 45102,
                "autonomous_system_organization": "Alibaba (US) Technology Co., Ltd.",
            }
        },
    )
    assert geoip.lookup_asn("47.82.10.78") == 45102
    assert geoip.lookup_asn_org("47.82.10.78") == "Alibaba (US) Technology Co., Ltd."


def test_lookup_asn_handles_missing_record(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_city(monkeypatch, {})
    _install_asn(monkeypatch, {})
    assert geoip.lookup_asn("203.0.113.99") == 0
    assert geoip.lookup_asn_org("203.0.113.99") == ""


def test_cache_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A lookup populates the cache and flush_cache writes the 6-tuple shape."""
    _install_city(
        monkeypatch,
        {
            "8.8.8.8": {
                "country": {"iso_code": "US", "names": {"en": "United States"}},
                "subdivisions": [{"names": {"en": "California"}}],
            }
        },
    )
    _install_asn(
        monkeypatch,
        {
            "8.8.8.8": {
                "autonomous_system_number": 15169,
                "autonomous_system_organization": "Google LLC",
            }
        },
    )
    assert geoip.lookup("8.8.8.8") == "US"
    assert geoip.lookup_name("8.8.8.8") == "United States"
    assert geoip.lookup_asn("8.8.8.8") == 15169
    geoip.flush_cache()

    cache_path = tmp_path / "lookup_cache.json"
    assert cache_path.exists()
    saved = json.loads(cache_path.read_text())
    assert "8.8.8.8" in saved
    code, name, sub, asn, asn_org, ts = saved["8.8.8.8"]
    assert code == "US"
    assert name == "United States"
    assert sub == "California"
    assert asn == 15169
    assert asn_org == "Google LLC"
    assert ts > 0


def test_cache_load_evicts_stale_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An entry older than the TTL is dropped at load time."""
    cache_path = tmp_path / "lookup_cache.json"
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    stale_ts = now_ts - (geoip._LOOKUP_CACHE_TTL_DAYS + 1) * 86400
    fresh_ts = now_ts - 60
    cache_path.write_text(
        json.dumps(
            {
                "1.1.1.1": [
                    "AU",
                    "Australia",
                    "New South Wales",
                    13335,
                    "Cloudflare, Inc.",
                    fresh_ts,
                ],
                "9.9.9.9": ["JP", "Japan", "Tokyo", 17676, "SoftBank Corp.", stale_ts],
            }
        )
    )
    monkeypatch.setattr(geoip, "_open_city_reader", lambda _db_dir: None)
    monkeypatch.setattr(geoip, "_open_asn_reader", lambda _db_dir: None)
    assert geoip.lookup("1.1.1.1") == "AU"
    assert geoip.lookup_asn("1.1.1.1") == 13335
    # 9.9.9.9 was evicted; with no reader, it resolves to "-" / 0.
    assert geoip.lookup("9.9.9.9") == "-"
    assert geoip.lookup_asn("9.9.9.9") == 0


def test_cache_load_handles_pre_asn_4tuple(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pre-ASN cache entries (4-tuple [code, name, sub, ts]) load the country
    fields and leave ASN unpopulated, so the next lookup_asn() fills them in."""
    cache_path = tmp_path / "lookup_cache.json"
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    cache_path.write_text(json.dumps({"8.8.8.8": ["US", "United States", "California", now_ts]}))
    _install_city(monkeypatch, {})
    _install_asn(
        monkeypatch,
        {
            "8.8.8.8": {
                "autonomous_system_number": 15169,
                "autonomous_system_organization": "Google LLC",
            }
        },
    )
    # Country fields come from the cache.
    assert geoip.lookup("8.8.8.8") == "US"
    assert geoip.lookup_name("8.8.8.8") == "United States"
    # ASN was missing in the cache → freshly populated from the mmdb.
    assert geoip.lookup_asn("8.8.8.8") == 15169
    assert geoip.lookup_asn_org("8.8.8.8") == "Google LLC"


def test_cache_load_ignores_legacy_2tuple(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Old `[code, ts]` entries from pre-City-Lite are discarded on load."""
    cache_path = tmp_path / "lookup_cache.json"
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    cache_path.write_text(
        json.dumps(
            {
                "1.1.1.1": ["AU", now_ts],  # legacy 2-tuple shape
                "8.8.8.8": [
                    "US",
                    "United States",
                    "California",
                    15169,
                    "Google LLC",
                    now_ts,
                ],
            }
        )
    )
    monkeypatch.setattr(geoip, "_open_city_reader", lambda _db_dir: None)
    monkeypatch.setattr(geoip, "_open_asn_reader", lambda _db_dir: None)
    # 1.1.1.1 had legacy shape → discarded → no reader → "-"
    assert geoip.lookup("1.1.1.1") == "-"
    # 8.8.8.8 had current shape → kept, including ASN
    assert geoip.lookup("8.8.8.8") == "US"
    assert geoip.lookup_asn("8.8.8.8") == 15169


def test_cache_load_tolerates_corrupt_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "lookup_cache.json").write_text("not json")
    _install_city(monkeypatch, {})
    _install_asn(monkeypatch, {})
    # Just doesn't crash.
    assert geoip.lookup("1.1.1.1") == "-"
    assert geoip.lookup_asn("1.1.1.1") == 0
