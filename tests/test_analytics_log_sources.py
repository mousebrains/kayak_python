"""Unit tests for the log/journal iterators in kayak.analytics._log_sources.

These exercise the parser primitives (combined-log, error-log,
journal, CSP JSON-per-line) against synthesized strings. The
real-disk + journalctl-subprocess wiring of the public iterators is
not exercised here — those are covered by manual smoke runs against
the live host (per PLAN_logs_analyze_migration.md Testing).
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import json
from pathlib import Path

import pytest

from kayak.analytics import _log_sources

UTC = dt.UTC
PDT = dt.timezone(dt.timedelta(hours=-7))


def test_combined_access_line_parses() -> None:
    """One synthetic kayak_timed line round-trips through the parser."""
    line = (
        "203.0.113.42 - - [15/May/2026:14:23:45 -0700] "
        '"GET /Oregon.html HTTP/2.0" 200 12345 '
        '"-" "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0" '
        "rt=0.012 urt=-\n"
    )
    iter_ = _log_sources._parse_access_file
    # Write to a Path that the helper can open
    tmp = Path("/tmp/_test_kayak_access.log")
    tmp.write_text(line)
    try:
        events = list(iter_(tmp))
    finally:
        tmp.unlink()
    assert len(events) == 1
    ev = events[0]
    assert ev.client == "203.0.113.42"
    assert ev.method == "GET"
    assert ev.path == "/Oregon.html"
    assert ev.status == 200
    assert ev.bytes_sent == 12345
    assert ev.rt == pytest.approx(0.012)
    assert ev.urt is None  # "-" → None
    assert ev.ts.year == 2026
    assert ev.ts.tzinfo is not None


def test_gzipped_access_file_parses() -> None:
    """The `.gz`-handling code path (relied on by rotated log files)."""
    line = (
        "198.51.100.10 - - [15/May/2026:08:00:00 -0700] "
        '"HEAD / HTTP/1.1" 200 0 '
        '"-" "curl/8.5.0" rt=0.001 urt=-\n'
    )
    tmp = Path("/tmp/_test_kayak_access.log.99.gz")
    with gzip.open(tmp, "wt") as f:
        f.write(line)
    try:
        events = list(_log_sources._parse_access_file(tmp))
    finally:
        tmp.unlink()
    assert len(events) == 1
    assert events[0].method == "HEAD"
    assert events[0].ua == "curl/8.5.0"


def test_combined_line_without_rt_urt_still_parses() -> None:
    """Legacy nginx log_format without the rt=/urt= suffix shouldn't crash."""
    line = (
        "203.0.113.50 - - [01/May/2026:00:00:00 -0700] "
        '"GET /favicon.ico HTTP/1.1" 200 318 "-" "Mozilla/5.0"\n'
    )
    tmp = Path("/tmp/_test_kayak_legacy.log")
    tmp.write_text(line)
    try:
        events = list(_log_sources._parse_access_file(tmp))
    finally:
        tmp.unlink()
    assert len(events) == 1
    assert events[0].rt is None
    assert events[0].urt is None


def test_malformed_access_line_skipped() -> None:
    """A garbage line shouldn't raise; just gets skipped."""
    tmp = Path("/tmp/_test_kayak_garbage.log")
    tmp.write_text("this is not an access log line\n")
    try:
        events = list(_log_sources._parse_access_file(tmp))
    finally:
        tmp.unlink()
    assert events == []


def test_error_log_multi_line_event() -> None:
    """nginx error logs can span lines; the builder coalesces them."""
    raw = (
        "2026/05/15 14:23:45 [error] 1234#0: *567890 FastCGI sent "
        'in stderr: "PHP message: example", client: 203.0.113.42, '
        'server: levels.mousebrains.com, request: "GET / HTTP/2.0", '
        'upstream: "fastcgi://...", host: "levels.mousebrains.com"\n'
        "2026/05/15 14:23:46 [warn] 1234#0: *567891 client closed "
        "connection while waiting for request\n"
    )
    tmp = Path("/tmp/_test_kayak_error.log")
    tmp.write_text(raw)
    try:
        events = list(_log_sources._parse_error_file(tmp, tz=PDT))
    finally:
        tmp.unlink()
    assert len(events) == 2
    assert events[0].level == "error"
    assert events[0].client == "203.0.113.42"
    assert events[0].request == "GET / HTTP/2.0"
    assert events[1].level == "warn"


def test_csp_json_per_line_parses() -> None:
    """CSP report log: one JSON object per line, malformed lines dropped."""
    entries = [
        {
            "ts": "2026-05-15T14:23:45+00:00",
            "ip": "203.0.113.42",
            "ua": "Mozilla/5.0",
            "document_uri": "https://levels.mousebrains.com/Oregon.html",
            "referrer": "https://levels.mousebrains.com/",
            "violated": "font-src 'self'",
        },
        {
            "ts": "2026-05-15T14:24:00+00:00",
            "ip": "203.0.113.43",
            "violated": "script-src 'self'",
        },
    ]
    body = "\n".join(json.dumps(e) for e in entries) + "\nNOT JSON\n\n"
    tmp = Path("/tmp/_test_kayak_csp.log")
    tmp.write_text(body)
    try:
        events = list(_log_sources._parse_csp_file(tmp))
    finally:
        tmp.unlink()
    assert len(events) == 2
    assert events[0].violated.startswith("font-src")
    assert events[1].document_uri == ""  # missing in entry 2 → coalesced to ""


def test_journal_regex_parses_short_iso() -> None:
    """`journalctl -o short-iso` line format."""
    line = (
        "2026-05-15T14:23:45-07:00 levels kayak-pipeline.service[12345]: "
        "Started Kayak data pipeline."
    )
    m = _log_sources._JOURNAL_RE.match(line)
    assert m is not None
    assert m["unit"] == "kayak-pipeline.service"
    assert m["msg"] == "Started Kayak data pipeline."


def test_in_memory_gzip_round_trip() -> None:
    """The plan's claim that .gz-handling can be tested with in-memory gzip."""
    text = (
        '10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] "GET / HTTP/1.1" 200 0 "-" "x" rt=0.1 urt=0.1\n'
    )
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(text.encode())
    # File-on-disk variant — _open_text only inspects suffix, not content
    tmp = Path("/tmp/_test_inmem_gzip.log.gz")
    tmp.write_bytes(buf.getvalue())
    try:
        events = list(_log_sources._parse_access_file(tmp))
    finally:
        tmp.unlink()
    assert len(events) == 1
    assert events[0].client == "10.0.0.1"
