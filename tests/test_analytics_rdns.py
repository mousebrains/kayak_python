"""Tests for the reverse-DNS cache + budgeted resolver in kayak.analytics.humans.

These cover the fixes for the 2026-05-23 kayak-status.service timeout: a
black-holed ``socket.gethostbyaddr`` must not pin the process (daemon threads +
wall-clock budget), and black-holed IPs must be negative-cached with an
exponential backoff instead of re-probed every run.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from kayak.analytics import humans


@pytest.fixture
def rdns_state(tmp_path, monkeypatch):
    """Isolate the module-global rdns caches + point the on-disk cache at tmp."""
    monkeypatch.setattr(humans, "_RDNS_CACHE_PATH", tmp_path / "rdns_cache.json")
    for d in (
        humans._rdns_cache,
        humans._rdns_last_seen,
        humans._rdns_retry_after,
        humans._rdns_fail_count,
    ):
        d.clear()
    humans._rdns_attempted.clear()
    monkeypatch.setattr(humans, "_rdns_cache_loaded", False)
    yield
    for d in (
        humans._rdns_cache,
        humans._rdns_last_seen,
        humans._rdns_retry_after,
        humans._rdns_fail_count,
    ):
        d.clear()
    humans._rdns_attempted.clear()


def _install_lookup(monkeypatch, resolved: dict[str, str], hang: set[str], gate: threading.Event):
    """Patch _rdns_lookup: resolved IPs return immediately; `hang` IPs block on
    `gate` (simulating a black-holed resolver). gate has a self-release timeout
    so a forgotten teardown can't wedge the suite."""

    def _lookup(ip: str) -> str:
        if ip in hang:
            gate.wait(timeout=30)
            return ""
        return resolved.get(ip, "")

    monkeypatch.setattr(humans, "_rdns_lookup", _lookup)


def test_resolve_parallel_bounds_wall_time_despite_blackhole(rdns_state, monkeypatch):
    # The core regression: a hanging gethostbyaddr must not extend wall time
    # past the budget, and must not block (daemon threads).
    gate = threading.Event()
    _install_lookup(monkeypatch, resolved={"1.1.1.1": "one.example."}, hang={"9.9.9.9"}, gate=gate)
    try:
        start = time.monotonic()
        resolved, started = humans._resolve_parallel(
            ["1.1.1.1", "9.9.9.9"], workers=4, budget_s=0.2
        )
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"resolver blocked {elapsed:.1f}s past its 0.2s budget"
        assert resolved == {"1.1.1.1": "one.example."}  # the black-hole didn't complete
        assert "9.9.9.9" in started  # but it was started, so the caller penalizes it
    finally:
        gate.set()


def test_blackhole_is_negative_cached_with_base_backoff(rdns_state, monkeypatch):
    gate = threading.Event()
    _install_lookup(monkeypatch, resolved={"1.1.1.1": "one.example."}, hang={"9.9.9.9"}, gate=gate)
    try:
        before = int(time.time())
        humans.warm_rdns(["1.1.1.1", "9.9.9.9"], workers=4, budget_s=0.2)
    finally:
        gate.set()
    # Confirmed lookup cached, no backoff state.
    assert humans._rdns_cache["1.1.1.1"] == "one.example."
    assert "1.1.1.1" not in humans._rdns_retry_after
    # Black-hole negative-cached with first-tier (7d) backoff.
    assert humans._rdns_cache["9.9.9.9"] == ""
    assert humans._rdns_fail_count["9.9.9.9"] == 1
    retry = humans._rdns_retry_after["9.9.9.9"]
    assert (
        before + humans._RDNS_NEG_RETRY_BASE_S
        <= retry
        <= int(time.time()) + humans._RDNS_NEG_RETRY_BASE_S
    )


def test_backoff_doubles_then_caps(rdns_state, monkeypatch):
    gate = threading.Event()
    _install_lookup(monkeypatch, resolved={}, hang={"9.9.9.9"}, gate=gate)
    base = humans._RDNS_NEG_RETRY_BASE_S
    cap = humans._RDNS_NEG_RETRY_MAX_S
    try:
        for prior_fail, expected_interval in [
            (1, base * 2),  # 2nd failure -> 14d
            (2, base * 4),  # 3rd failure -> 28d
            (4, base * 16),  # 5th failure -> 112d
            (10, cap),  # deep failure -> capped at ~26 weeks
        ]:
            # Seed as if loaded from disk: negative, retry window already open.
            humans._rdns_cache["9.9.9.9"] = ""
            humans._rdns_retry_after["9.9.9.9"] = int(time.time()) - 1
            humans._rdns_fail_count["9.9.9.9"] = prior_fail
            humans._rdns_attempted.clear()
            before = int(time.time())
            humans.warm_rdns(["9.9.9.9"], workers=2, budget_s=0.15)
            assert humans._rdns_fail_count["9.9.9.9"] == prior_fail + 1
            got = humans._rdns_retry_after["9.9.9.9"] - before
            assert abs(got - expected_interval) <= 2, (
                f"fail {prior_fail}->{prior_fail + 1}: want ~{expected_interval}s, got {got}s"
            )
    finally:
        gate.set()


def test_future_retry_window_suppresses_lookup(rdns_state, monkeypatch):
    # An IP whose negative-cache retry is still in the future must not be probed.
    def _boom(_ip: str) -> str:
        raise AssertionError("lookup attempted while retry window still closed")

    monkeypatch.setattr(humans, "_rdns_lookup", _boom)
    humans._rdns_cache["9.9.9.9"] = ""
    humans._rdns_retry_after["9.9.9.9"] = int(time.time()) + 100_000
    humans._rdns_fail_count["9.9.9.9"] = 2
    humans.warm_rdns(["9.9.9.9"], workers=2, budget_s=0.2)  # must not raise
    assert humans._rdns_fail_count["9.9.9.9"] == 2  # unchanged


def test_confirmed_result_clears_prior_backoff(rdns_state, monkeypatch):
    gate = threading.Event()
    _install_lookup(
        monkeypatch, resolved={"9.9.9.9": "now-resolves.example."}, hang=set(), gate=gate
    )
    # Seed a backed-off negative whose window has opened.
    humans._rdns_cache["9.9.9.9"] = ""
    humans._rdns_retry_after["9.9.9.9"] = int(time.time()) - 1
    humans._rdns_fail_count["9.9.9.9"] = 3
    try:
        humans.warm_rdns(["9.9.9.9"], workers=2, budget_s=0.2)
    finally:
        gate.set()
    assert humans._rdns_cache["9.9.9.9"] == "now-resolves.example."
    assert "9.9.9.9" not in humans._rdns_retry_after
    assert "9.9.9.9" not in humans._rdns_fail_count


def test_cache_roundtrips_negative_backoff_and_confirmed(rdns_state, monkeypatch):
    now = int(time.time())
    humans._rdns_cache["8.8.8.8"] = "dns.google."
    humans._rdns_last_seen["8.8.8.8"] = now
    humans._rdns_cache["9.9.9.9"] = ""
    humans._rdns_last_seen["9.9.9.9"] = now
    humans._rdns_retry_after["9.9.9.9"] = now + humans._RDNS_NEG_RETRY_BASE_S
    humans._rdns_fail_count["9.9.9.9"] = 2
    humans._save_rdns_cache_to_disk()

    # On-disk shapes: 2-list for confirmed, 4-list for negative.
    on_disk = json.loads(humans._RDNS_CACHE_PATH.read_text())
    assert on_disk["8.8.8.8"] == ["dns.google.", now]
    assert on_disk["9.9.9.9"] == ["", now, now + humans._RDNS_NEG_RETRY_BASE_S, 2]

    # Reload into fresh in-memory state.
    for d in (
        humans._rdns_cache,
        humans._rdns_last_seen,
        humans._rdns_retry_after,
        humans._rdns_fail_count,
    ):
        d.clear()
    monkeypatch.setattr(humans, "_rdns_cache_loaded", False)
    humans._load_rdns_cache_from_disk()
    assert humans._rdns_cache["8.8.8.8"] == "dns.google."
    assert humans._rdns_retry_after["9.9.9.9"] == now + humans._RDNS_NEG_RETRY_BASE_S
    assert humans._rdns_fail_count["9.9.9.9"] == 2


def test_load_tolerates_legacy_and_evicts_stale(rdns_state, monkeypatch):
    now = int(time.time())
    stale = now - (humans._RDNS_CACHE_TTL_DAYS + 1) * 86400
    humans._RDNS_CACHE_PATH.write_text(
        json.dumps(
            {
                "1.1.1.1": "legacy-string.example.",  # legacy name-only shape
                "2.2.2.2": ["fresh.example.", now],  # confirmed, fresh
                "3.3.3.3": ["old.example.", stale],  # confirmed, past TTL -> evicted
            }
        )
    )
    humans._load_rdns_cache_from_disk()
    assert humans._rdns_cache["1.1.1.1"] == "legacy-string.example."
    assert humans._rdns_cache["2.2.2.2"] == "fresh.example."
    assert "3.3.3.3" not in humans._rdns_cache


def test_saturated_pool_does_not_poison_unpulled_queue(rdns_state, monkeypatch):
    # The bug a review caught: when every worker is stuck on a black-hole, the
    # IPs still sitting in the queue are never attempted — they must NOT be
    # negative-cached as if they had black-holed (that would suppress valid PTRs).
    gate = threading.Event()
    targets = [f"9.9.9.{i}" for i in range(6)]
    _install_lookup(monkeypatch, resolved={}, hang=set(targets), gate=gate)
    try:
        # 2 workers, all 6 hang -> only 2 ever get pulled/started.
        humans.warm_rdns(targets, workers=2, budget_s=0.2)
    finally:
        gate.set()
    penalized = [ip for ip in targets if ip in humans._rdns_retry_after]
    assert len(penalized) == 2, f"expected only the 2 started IPs penalized, got {penalized}"
    # The 4 never-pulled IPs are left uncached for a clean retry next run.
    uncached = [ip for ip in targets if ip not in humans._rdns_cache]
    assert len(uncached) == 4


def test_repeated_calls_dedup_targets_to_bound_budget(rdns_state, monkeypatch):
    # _rdns_attempted ensures the per-render budget is spent at most once even
    # though the status render calls warm_rdns six times over the same IP set.
    calls: list[str] = []

    def _lookup(ip: str) -> str:
        calls.append(ip)
        return "host.example."

    monkeypatch.setattr(humans, "_rdns_lookup", _lookup)
    humans.warm_rdns(["1.1.1.1", "2.2.2.2"], workers=4, budget_s=1.0)
    assert sorted(calls) == ["1.1.1.1", "2.2.2.2"]
    humans.warm_rdns(["1.1.1.1", "2.2.2.2"], workers=4, budget_s=1.0)
    assert len(calls) == 2  # second call attempts nothing new


def test_load_negative_without_fail_count_defaults_to_one(rdns_state):
    # A 3-element (retry_after but no fail_count) entry must hydrate fail_count=1
    # so a later re-fail escalates the backoff instead of resetting to tier 1.
    now = int(time.time())
    humans._RDNS_CACHE_PATH.write_text(json.dumps({"9.9.9.9": ["", now, now + 1000]}))
    humans._load_rdns_cache_from_disk()
    assert humans._rdns_retry_after["9.9.9.9"] == now + 1000
    assert humans._rdns_fail_count["9.9.9.9"] == 1
