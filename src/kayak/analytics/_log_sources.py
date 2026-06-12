"""Log + journal iterators backing ``levels analyze-logs``.

Replaces syncit's harvest step. Each iterator takes a ``since``
cutoff (timezone-aware datetime) and yields parsed events newer than
that — callers don't have to filter again.

The combined-log / error-log / journal regexes are copied verbatim
from the months-debugged ``~/logs.analyze/analyze.py`` versions; only
the file-source plumbing changes (direct ``/var/log`` glob instead of
a harvest dir).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import gzip
import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import IO, NamedTuple


class AccessEvent(NamedTuple):
    ts: dt.datetime
    client: str
    method: str | None
    path: str | None
    status: int
    bytes_sent: int
    ua: str
    rt: float | None
    urt: float | None


class ErrorEvent(NamedTuple):
    ts: dt.datetime
    level: str
    pid: str
    msg: str
    client: str | None
    request: str | None


class UnitEvent(NamedTuple):
    ts: dt.datetime
    unit: str
    msg: str


class CspEvent(NamedTuple):
    ts: dt.datetime
    ip: str
    ua: str
    document_uri: str
    referrer: str
    violated: str
    raw: dict[str, object]


# nginx kayak_timed log_format: combined + rt=<request_time> urt=<upstream_response_time>.
# See /etc/nginx/conf.d/kayak-log-format.conf.
_COMBINED_RE = re.compile(
    r"^(?P<client>\S+) \S+ \S+ "
    r"\[(?P<ts>[^\]]+)\] "
    r'"(?P<req>[^"]*)" '
    r"(?P<status>\d+) (?P<bytes>\S+) "
    r'"[^"]*" '
    r'"(?P<ua>[^"]*)"'
    r"(?:\s+rt=(?P<rt>\S+)\s+urt=(?P<urt>\S+))?"
    r"\s*$"
)

_REQ_RE = re.compile(r"^(\S+) (\S+)(?:\s+HTTP/\S+)?$")

_ERROR_HEAD_RE = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) "
    r"\[(?P<level>\w+)\] "
    r"(?P<pid>\d+)#\d+:"
)
_ERROR_BODY_TAIL_RE = re.compile(
    r"(?P<msg>.*?)"
    r"(?:, client: (?P<client>\S+?),"
    r" server: \S+?,"
    r' request: "(?P<request>[^"]*)")?'
    r"(?:,.*)?$",
    re.DOTALL,
)

# journalctl -o short-iso writes a single line per event:
#   2026-05-15T12:34:56-07:00 hostname unit-name[pid]: message
_JOURNAL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)) "
    r"\S+ "
    r"(?P<unit>\S+?)(?:\[\d+\])?: "
    r"(?P<msg>.*)$"
)


def _open_text(path: Path) -> IO[str]:
    """Open a log file, transparently decompressing ``.gz``."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", errors="replace")
    return open(path, errors="replace")


def _parse_access_ts(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None


def _parse_dur(s: str | None) -> float | None:
    """Parse an nginx duration field. ``-`` → None, comma lists → max."""
    if s is None or s == "" or s == "-":
        return None
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip() not in ("", "-")]
        vals = []
        for p in parts:
            with contextlib.suppress(ValueError):
                vals.append(float(p))
        return max(vals) if vals else None
    try:
        return float(s)
    except ValueError:
        return None


def _glob_sorted(pattern: str) -> list[Path]:
    """Sorted list of files matching a glob (lex order — analyze.py-compatible)."""
    return sorted(Path("/").glob(pattern.lstrip("/")))


def _parse_access_file(path: Path) -> Iterator[AccessEvent]:
    with _open_text(path) as f:
        for line in f:
            m = _COMBINED_RE.match(line)
            if not m:
                continue
            ts = _parse_access_ts(m["ts"])
            if ts is None:
                continue
            method = path_q = None
            mr = _REQ_RE.match(m["req"])
            if mr:
                method, path_q = mr.group(1), mr.group(2)
            try:
                status = int(m["status"])
            except ValueError:
                continue
            try:
                bytes_sent = int(m["bytes"])
            except ValueError:
                bytes_sent = 0
            rt = _parse_dur(m.group("rt"))
            urt = _parse_dur(m.group("urt"))
            yield AccessEvent(ts, m["client"], method, path_q, status, bytes_sent, m["ua"], rt, urt)


def iter_access_events(
    since: dt.datetime,
    log_glob: str = "/var/log/nginx/*access.log*",
) -> Iterator[AccessEvent]:
    """All nginx access events from kayak vhosts at or after ``since``.

    Default glob matches every access log under the nginx log directory;
    narrow with ``log_glob`` for a single vhost (vhost log names are host
    configuration — parameterized by S7).
    """
    for path in _glob_sorted(log_glob):
        for ev in _parse_access_file(path):
            if ev.ts >= since:
                yield ev


def iter_blocked_events(
    since: dt.datetime,
    log_glob: str = "/var/log/nginx/blocked-access.log*",
) -> Iterator[AccessEvent]:
    """Same combined format as access logs — 444 drops the catch-all vhost emits."""
    for path in _glob_sorted(log_glob):
        for ev in _parse_access_file(path):
            if ev.ts >= since:
                yield ev


def _build_error(buf: list[str], tz: dt.tzinfo) -> ErrorEvent | None:
    s = "".join(buf)
    m = _ERROR_HEAD_RE.match(s)
    if not m:
        return None
    try:
        naive = dt.datetime.strptime(m["ts"], "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None
    ts = naive.replace(tzinfo=tz)
    body = s[m.end() :].lstrip()
    body = re.sub(r"^\*\d+\s*", "", body).rstrip()
    mt = _ERROR_BODY_TAIL_RE.match(body)
    if mt:
        msg = mt.group("msg").strip()
        client = mt.group("client")
        request = mt.group("request")
    else:
        msg = body
        client = None
        request = None
    return ErrorEvent(ts, m["level"], m["pid"], msg, client, request)


def _parse_error_file(path: Path, tz: dt.tzinfo) -> Iterator[ErrorEvent]:
    buf: list[str] = []
    with _open_text(path) as f:
        for line in f:
            if _ERROR_HEAD_RE.match(line):
                if buf:
                    ev = _build_error(buf, tz)
                    if ev:
                        yield ev
                buf = [line]
            elif buf:
                buf.append(line)
        if buf:
            ev = _build_error(buf, tz)
            if ev:
                yield ev


def iter_error_events(
    since: dt.datetime,
    tz: dt.tzinfo,
    log_glob: str = "/var/log/nginx/*error.log*",
) -> Iterator[ErrorEvent]:
    """nginx error events at or after ``since``. Naive nginx timestamps get ``tz``."""
    for path in _glob_sorted(log_glob):
        for ev in _parse_error_file(path, tz):
            if ev.ts >= since:
                yield ev


def iter_unit_events(
    since: dt.datetime,
    units_pattern: str = "kayak-*",
) -> Iterator[UnitEvent]:
    """journalctl --since=<since> -u <units_pattern> -o short-iso, parsed.

    Subprocess output is captured as text; an empty journal (no
    matching units) yields zero events without erroring. journalctl
    must be available on PATH — true on systemd hosts.
    """
    # journalctl accepts ISO timestamps via --since.
    proc = subprocess.run(
        [
            "journalctl",
            "--since",
            since.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "-u",
            units_pattern,
            "-o",
            "short-iso",
            "--no-pager",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        # Soft-fail: surface stderr to caller's log, yield nothing.
        return
    for line in proc.stdout.splitlines():
        m = _JOURNAL_RE.match(line)
        if not m:
            continue
        ts_raw = m["ts"]
        if ts_raw.endswith("Z"):
            ts_raw = ts_raw[:-1] + "+00:00"
        try:
            ts = dt.datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if ts >= since:
            yield UnitEvent(ts, m["unit"], m["msg"])


def _parse_csp_file(path: Path) -> Iterator[CspEvent]:
    """JSON-per-line entries. Lines that fail to parse are dropped silently."""
    with _open_text(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            ts_raw = obj.get("ts")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = dt.datetime.fromisoformat(ts_raw)
            except ValueError:
                continue
            yield CspEvent(
                ts=ts,
                ip=str(obj.get("ip", "")),
                ua=str(obj.get("ua", "")),
                document_uri=str(obj.get("document_uri", "")),
                referrer=str(obj.get("referrer", "")),
                violated=str(obj.get("violated", "")),
                raw=obj,
            )


def iter_csp_events(
    since: dt.datetime,
    log_glob: str = "/home/pat/logs/csp.log*",
) -> Iterator[CspEvent]:
    """CSP violation reports written by ``src/kayak/web/php/csp-report.php``.

    Default path matches the live host's www-data-writable ACL'd
    location. Missing log files yield zero events (csp-report.php
    only writes when CSP violations occur — empty on quiet weeks).
    """
    for path in _glob_sorted(log_glob):
        for ev in _parse_csp_file(path):
            if ev.ts >= since:
                yield ev
