"""Tests for kayak.analytics.ip_reputation (FireHOL Level 1).

Stubs the network fetcher so tests don't depend on iplists.firehol.org
being reachable. Validates the netset parser + sorted-interval lookup
against a synthetic blocklist.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from kayak.analytics import ip_reputation


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ip_reputation, "_FIREHOL_CACHE_PATH", tmp_path / "firehol_level1.netset")
    ip_reputation.reset_cache_for_tests()


_SAMPLE_NETSET = """\
#
# firehol_level1 (synthetic test fixture)
#
1.10.16.0/20
2.59.152.0/23
192.0.2.0/24
10.0.0.0/8
2001:db8::/32
"""


def _install_cached_netset(tmp_path: Path) -> None:
    (tmp_path / "firehol_level1.netset").write_text(_SAMPLE_NETSET)


def test_lookup_finds_v4_blocked_ip(tmp_path: Path) -> None:
    _install_cached_netset(tmp_path)
    assert ip_reputation.is_firehol_blocked("1.10.16.5")
    assert ip_reputation.is_firehol_blocked("2.59.152.42")
    assert ip_reputation.is_firehol_blocked("192.0.2.100")


def test_lookup_finds_v6_blocked_ip(tmp_path: Path) -> None:
    _install_cached_netset(tmp_path)
    assert ip_reputation.is_firehol_blocked("2001:db8::1")
    assert ip_reputation.is_firehol_blocked("2001:db8:ffff::1")


def test_lookup_rejects_non_blocked_ip(tmp_path: Path) -> None:
    _install_cached_netset(tmp_path)
    assert not ip_reputation.is_firehol_blocked("8.8.8.8")
    assert not ip_reputation.is_firehol_blocked("76.115.247.180")
    assert not ip_reputation.is_firehol_blocked("2607:f8b0::4")


def test_lookup_handles_invalid_ip(tmp_path: Path) -> None:
    _install_cached_netset(tmp_path)
    assert not ip_reputation.is_firehol_blocked("not-an-ip")
    assert not ip_reputation.is_firehol_blocked("")


def test_no_disk_no_network_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    # No cache file + network returns None → all lookups return False.
    monkeypatch.setattr(ip_reputation, "_try_fetch_firehol", lambda: None)
    assert not ip_reputation.is_firehol_blocked("1.10.16.5")


def test_stale_disk_triggers_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "firehol_level1.netset"
    cache.write_text("# old data\n4.4.4.0/24\n")
    old = dt.datetime.now().timestamp() - ip_reputation._FIREHOL_TTL_S - 3600
    os.utime(cache, (old, old))

    # Stale → fetcher returns fresh netset → new ranges loaded
    monkeypatch.setattr(ip_reputation, "_try_fetch_firehol", lambda: _SAMPLE_NETSET)
    assert ip_reputation.is_firehol_blocked("1.10.16.5")
    # The stale 4.4.4.0/24 is no longer present after refresh
    assert not ip_reputation.is_firehol_blocked("4.4.4.4")


def test_netset_parser_ignores_comments_and_blanks(tmp_path: Path) -> None:
    (tmp_path / "firehol_level1.netset").write_text(
        "# leading comment\n\n   \n5.5.5.0/24\n# mid comment\n6.6.6.6\n\n"
    )
    assert ip_reputation.is_firehol_blocked("5.5.5.5")
    assert ip_reputation.is_firehol_blocked("6.6.6.6")
    assert not ip_reputation.is_firehol_blocked("7.7.7.7")


def test_netset_parser_skips_malformed_lines(tmp_path: Path) -> None:
    (tmp_path / "firehol_level1.netset").write_text(
        "5.5.5.0/24\nnot a cidr\n999.999.999.999\n6.6.6.6\n"
    )
    assert ip_reputation.is_firehol_blocked("5.5.5.5")
    assert ip_reputation.is_firehol_blocked("6.6.6.6")
