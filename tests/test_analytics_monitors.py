"""Tests for kayak.analytics.monitors (Better Stack IP list)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.analytics import monitors


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Each test gets its own cache file so they don't poison real state."""
    monkeypatch.setattr(monitors, "_BETTERSTACK_CACHE_PATH", tmp_path / "betterstack.json")
    monitors.reset_cache_for_tests()


def test_is_betterstack_uses_disk_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Pre-populate the disk cache so no network call is made.
    (tmp_path / "betterstack.json").write_text(
        '{"us": ["5.161.118.194"], "eu": ["91.98.38.26", "2a01:4f8:c013:3a76::1"]}'
    )
    # Make sure the fetcher isn't called if disk cache is fresh.
    monkeypatch.setattr(monitors, "_try_fetch_betterstack", lambda: pytest.fail("must not fetch"))
    assert monitors.is_betterstack("5.161.118.194")
    assert monitors.is_betterstack("91.98.38.26")
    assert monitors.is_betterstack("2a01:4f8:c013:3a76::1")
    assert not monitors.is_betterstack("8.8.8.8")


def test_is_betterstack_no_disk_no_network_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No disk cache and network fetch fails — should fail-open (no false positives).
    monkeypatch.setattr(monitors, "_try_fetch_betterstack", lambda: None)
    assert monitors.is_betterstack("5.161.118.194") is False


def test_is_betterstack_fetches_when_disk_cache_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Write a stale cache (mtime older than TTL).
    cache = tmp_path / "betterstack.json"
    cache.write_text('{"us": ["1.1.1.1"]}')
    import os

    old = __import__("datetime").datetime.now().timestamp() - monitors._BETTERSTACK_TTL_S - 3600
    os.utime(cache, (old, old))

    # Fetcher returns new data.
    monkeypatch.setattr(monitors, "_try_fetch_betterstack", lambda: {"us": ["2.2.2.2"]})
    assert monitors.is_betterstack("2.2.2.2")
    assert not monitors.is_betterstack("1.1.1.1")


def test_is_betterstack_handles_malformed_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(monitors, "_try_fetch_betterstack", lambda: {"bad": "not-a-list"})
    assert monitors.is_betterstack("anything") is False
