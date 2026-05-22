"""Tests for kayak.analytics.privacy_relays (Apple iCloud Private Relay).

Stubs the network fetcher so the tests don't depend on Apple's mask-api
endpoint being reachable. Validates the parse + sorted-interval lookup
against a handful of synthetic CIDRs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.analytics import privacy_relays


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(privacy_relays, "_APPLE_CACHE_PATH", tmp_path / "icloud-relay.csv")
    privacy_relays.reset_cache_for_tests()


_SAMPLE_CSV = """\
172.224.226.0/27,GB,GB-EN,London,
146.75.136.0/22,US,US-WA,BREMERTON,
2a04:4e41:275f:1700::/56,US,US-WA,SEATTLE,
2a09:bac2:b089:100::/56,US,US-OR,Oregon City,
"""


def _install_cached_csv(tmp_path: Path) -> None:
    (tmp_path / "icloud-relay.csv").write_text(_SAMPLE_CSV)


def test_lookup_finds_v4_relay_ip(tmp_path: Path) -> None:
    _install_cached_csv(tmp_path)
    assert privacy_relays.is_apple_private_relay("146.75.136.225")
    assert privacy_relays.apple_relay_region("146.75.136.225") == ("US", "US-WA", "BREMERTON")


def test_lookup_finds_v6_relay_ip(tmp_path: Path) -> None:
    _install_cached_csv(tmp_path)
    assert privacy_relays.is_apple_private_relay("2a04:4e41:275f:1766::3f5f:1766")
    assert privacy_relays.apple_relay_region("2a04:4e41:275f:1766::3f5f:1766") == (
        "US",
        "US-WA",
        "SEATTLE",
    )


def test_lookup_rejects_non_relay_ip(tmp_path: Path) -> None:
    _install_cached_csv(tmp_path)
    assert not privacy_relays.is_apple_private_relay("8.8.8.8")
    assert privacy_relays.apple_relay_region("8.8.8.8") is None


def test_lookup_handles_invalid_ip(tmp_path: Path) -> None:
    _install_cached_csv(tmp_path)
    assert not privacy_relays.is_apple_private_relay("not-an-ip")
    assert privacy_relays.apple_relay_region("not-an-ip") is None


def test_no_disk_no_network_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacy_relays, "_try_fetch_apple_csv", lambda: None)
    assert not privacy_relays.is_apple_private_relay("146.75.136.225")


def test_stale_disk_triggers_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import datetime as dt
    import os

    cache = tmp_path / "icloud-relay.csv"
    cache.write_text("1.1.1.1/32,AU,AU-NSW,Sydney,\n")
    old = dt.datetime.now().timestamp() - privacy_relays._APPLE_TTL_S - 3600
    os.utime(cache, (old, old))

    monkeypatch.setattr(privacy_relays, "_try_fetch_apple_csv", lambda: _SAMPLE_CSV)
    # Stale → fetcher returns fresh CSV → BREMERTON CIDR included
    assert privacy_relays.is_apple_private_relay("146.75.136.225")


def test_apple_relay_region_returns_none_for_unknown_ip(tmp_path: Path) -> None:
    _install_cached_csv(tmp_path)
    # Outside any CIDR in our sample
    assert privacy_relays.apple_relay_region("10.0.0.1") is None
