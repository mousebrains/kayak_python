"""Smoke tests for kayak.analytics.release_postmortem.

Each analyze_* function takes pre-materialized event lists + window
tuples, returns Markdown. These tests feed synthesized events and
assert on key strings in the output. Field-level math correctness is
verified by the months-of-operator-use history that the original
analyze.py earned; the test goal here is just "structure intact +
no exceptions on empty/edge inputs."
"""

from __future__ import annotations

import datetime as dt

from kayak.analytics import release_postmortem as rp
from kayak.analytics._log_sources import AccessEvent, CspEvent, ErrorEvent, UnitEvent

UTC = dt.UTC


def _ts(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=UTC)


def _windows() -> tuple[tuple[dt.datetime, dt.datetime], tuple[dt.datetime, dt.datetime]]:
    release = _ts(2026, 5, 15, 12)
    baseline = (release - dt.timedelta(hours=48), release)
    post = (release, release + dt.timedelta(hours=12))
    return baseline, post


def test_analyze_systemd_units_empty_event_stream() -> None:
    baseline, post = _windows()
    out = rp.analyze_systemd_units([], release=baseline[1], post_hi=post[1])
    assert "Systemd units" in out
    assert "no journal events" in out


def test_analyze_systemd_units_renders_failure() -> None:
    baseline, post = _windows()
    release = baseline[1]
    events = [
        UnitEvent(
            ts=release + dt.timedelta(minutes=5),
            unit="systemd",
            msg="kayak-pipeline.service: Failed with result 'exit-code'.",
        ),
    ]
    out = rp.analyze_systemd_units(events, release=release, post_hi=post[1])
    assert "FAIL" in out
    assert "kayak-pipeline.service" in out


def test_analyze_http_status_no_traffic() -> None:
    baseline, post = _windows()
    out = rp.analyze_http_status([], baseline=baseline, post=post)
    assert "no kayak-access traffic" in out


def test_analyze_http_status_flags_new_5xx() -> None:
    baseline, post = _windows()
    release = baseline[1]
    events = [
        # Baseline: 2 successful hits on /
        AccessEvent(
            ts=baseline[0] + dt.timedelta(hours=1),
            client="1.1.1.1",
            method="GET",
            path="/",
            status=200,
            bytes_sent=1000,
            ua="UA",
            rt=0.05,
            urt=0.04,
        ),
        # Post: 1 hit with a 500
        AccessEvent(
            ts=release + dt.timedelta(minutes=10),
            client="1.1.1.1",
            method="GET",
            path="/",
            status=500,
            bytes_sent=100,
            ua="UA",
            rt=0.5,
            urt=0.4,
        ),
    ]
    out = rp.analyze_http_status(events, baseline=baseline, post=post)
    assert "WARN" in out
    assert "new 5xx" in out


def test_analyze_error_clusters_empty() -> None:
    baseline, post = _windows()
    out = rp.analyze_error_clusters([], baseline=baseline, post=post)
    assert "no error-log entries" in out


def test_analyze_stale_deploy_no_hits() -> None:
    _baseline, post = _windows()
    out = rp.analyze_stale_deploy([], post=post, tz=UTC)
    assert "Verdict: OK" in out


def test_analyze_stale_deploy_flags_epoch_hit() -> None:
    baseline, post = _windows()
    release = baseline[1]
    events = [
        ErrorEvent(
            ts=release + dt.timedelta(minutes=1),
            level="error",
            pid="123",
            msg='open() "/home/pat/public_html_1234567890/foo" failed',
            client="1.1.1.1",
            request="GET /foo HTTP/1.1",
        ),
    ]
    out = rp.analyze_stale_deploy(events, post=post, tz=UTC)
    assert "WARN" in out
    assert "1234567890" in out


def test_analyze_new_404s_no_traffic() -> None:
    baseline, post = _windows()
    out = rp.analyze_new_404s([], baseline=baseline, post=post)
    assert "no 404s" in out


def test_analyze_blocked_delta_empty() -> None:
    baseline, post = _windows()
    out = rp.analyze_blocked_delta([], baseline=baseline, post=post)
    assert "no blocked-access traffic" in out


def test_analyze_slow_routes_no_rt_field() -> None:
    baseline, post = _windows()
    # Events without rt= populated
    events = [
        AccessEvent(
            ts=baseline[0] + dt.timedelta(hours=1),
            client="1.1.1.1",
            method="GET",
            path="/",
            status=200,
            bytes_sent=1000,
            ua="UA",
            rt=None,
            urt=None,
        )
    ]
    out = rp.analyze_slow_routes(events, baseline=baseline, post=post)
    assert "log_format doesn't emit" in out


def test_analyze_csp_no_rows() -> None:
    baseline, post = _windows()
    out = rp.analyze_csp([], baseline=baseline, post=post)
    assert "no csp.log entries" in out


def test_analyze_csp_flags_new_violation() -> None:
    baseline, post = _windows()
    release = baseline[1]
    events = [
        CspEvent(
            ts=release + dt.timedelta(minutes=1),
            ip="1.1.1.1",
            ua="UA",
            document_uri="https://levels.mousebrains.com/Oregon.html",
            referrer="",
            violated="font-src",
            raw={"blocked": "https://example.com/font.woff2"},
        )
    ]
    out = rp.analyze_csp(events, baseline=baseline, post=post)
    assert "WARN" in out
    assert "font-src" in out


def test_analyze_gaps_reports_both_signals() -> None:
    # access has rt, csp has at least one event → both yes
    access = [
        AccessEvent(
            ts=_ts(2026, 5, 15),
            client="1.1.1.1",
            method="GET",
            path="/",
            status=200,
            bytes_sent=0,
            ua="UA",
            rt=0.1,
            urt=0.1,
        )
    ]
    csps = [
        CspEvent(
            ts=_ts(2026, 5, 15),
            ip="1.1.1.1",
            ua="UA",
            document_uri="",
            referrer="",
            violated="font-src",
            raw={},
        )
    ]
    out = rp.analyze_gaps(access, csps)
    assert "rt=$request_time" in out
    assert "yes" in out


def test_render_header_includes_release_and_db_health() -> None:
    baseline, post = _windows()
    out = rp.render_header(
        release=baseline[1],
        baseline=baseline,
        post=post,
        git_commits=["abc123 2026-05-15 12:00:00 +0000 Pat fix something"],
        db_health={"observation_count": "12345", "schema_head": "42"},
        deploy_listing=["l 0 2026-05-15T12:00:00 /opt/kayak/current -> releases/abc123"],
    )
    assert "Post-release log analysis" in out
    assert "Release:" in out
    assert "abc123" in out
    assert "observation_count" in out
    assert "/opt/kayak/current" in out
