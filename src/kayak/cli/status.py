"""``levels status`` — operator status-page renderer.

Produces a single self-contained HTML file summarizing host + project
health for the operator. Designed to be served behind
``require_maintainer()`` at ``/_internal/status`` on levels.wkcc.org;
regenerated nightly by ``kayak-status.timer``.

Five sections (top to bottom):
    1. Header + cross-links to /_internal/ (live dashboard) and /status.json
    2. Traffic 24h — humans/bots/other buckets + per-IP detail
    3. Disk & memory — df + /proc/meminfo with WARN/FAIL flags
    4. systemd jobs — per-kayak-* service state + recent errors
    5. Backups + cert — hourly/weekly/offsite freshness + TLS leaf expiry

Public PUBLIC status snapshot is /status.json (src/kayak/web/php/status.php) — separate.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import glob
import html
import os
import socket
import subprocess
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown

from kayak.analytics import humans

_DEFAULT_TZ = "America/Los_Angeles"
# Narrower than the analyze-logs default (which uses ``*access.log*``) — the
# nginx default vhost's blocked-access.log catches port scanners sending raw
# TLS/SOCKS/RDP/MongoDB bytes, which produce unparseable "request lines" and
# show up here as an empty path. Those aren't requests to levels.wkcc.org so
# they don't belong in the operator status page.
_DEFAULT_LOG_GLOB = "/var/log/nginx/levels-*.access.log*"
_DEFAULT_OUTPUT = "/home/pat/var/status.html"

# Mirror of scripts/health-check.sh defaults (the user's "aggressive" set).
DISK_WARN_PCT = 70
DISK_FAIL_PCT = 85
SWAP_USED_PCT_WARN = 10
MEM_FREE_MB_WARN = 400

# Backup-age thresholds (seconds).
HOURLY_WARN_S = 2 * 3600
WEEKLY_WARN_S = 8 * 86400
OFFSITE_WARN_S = 32 * 86400

# Cert expiry threshold (days).
CERT_WARN_DAYS = 30

_INLINE_CSS = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       margin: 1.5rem auto; max-width: 1100px; padding: 0 1rem; color: #1a1a1a; }
h1 { margin-bottom: 0.25rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; }
h3 { margin-top: 1.25rem; }
.meta { color: #555; font-size: 0.9rem; margin-bottom: 1.5rem; }
.meta a { color: #0066cc; margin-right: 0.75rem; }
table { border-collapse: collapse; margin: 0.5rem 0 1rem; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; font-size: 0.9rem; }
th { background: #f4f4f4; }
tr.warn td:first-child, td.warn { background: #fff3cd; color: #664d03; font-weight: 600; }
tr.fail td:first-child, td.fail { background: #f8d7da; color: #58151c; font-weight: 700; }
tr.ok td:first-child, td.ok { color: #155724; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }
pre { background: #f7f7f7; padding: 6px 10px; border: 1px solid #ddd;
      overflow-x: auto; max-width: 100%; }
.error-list { margin: 0.25rem 0 0.75rem; padding-left: 1.25rem; }
.error-list li { font-family: ui-monospace, monospace; font-size: 0.8rem; color: #58151c; }
.muted { color: #777; }
.unit-name { font-family: ui-monospace, monospace; font-size: 0.85rem; }
details.collapsible { margin: 0.5rem 0 1.5rem; }
details.collapsible > summary { cursor: pointer; color: #0066cc; font-weight: 600;
    padding: 0.4rem 0; user-select: none; font-size: 1.05rem; }
details.collapsible > summary:hover { text-decoration: underline; }
details.collapsible[open] > summary { margin-bottom: 0.5rem; }
details.collapsible > summary .meta { color: #555; font-weight: normal;
    font-size: 0.9rem; margin-left: 0.5rem; }
details.collapsible > summary .badge-fail { background: #f8d7da; color: #58151c;
    padding: 1px 8px; border-radius: 4px; font-size: 0.85em; font-weight: 700;
    margin-left: 0.5rem; }
"""


def _run(
    cmd: list[str], *, timeout: int = 15, stdin_text: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        input=stdin_text,
    )


def _fmt_gb(kb: float) -> str:
    return f"{kb / 1024 / 1024:.2f} GB"


def _fmt_mb(kb: float) -> str:
    return f"{kb / 1024:.0f} MB"


def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _parse_systemd_timestamp(value: str) -> dt.datetime | None:
    """Parse 'Thu 2026-05-21 15:16:24 PDT' style timestamps."""
    if not value or value in ("n/a", "0"):
        return None
    try:
        parts = value.split()
        if len(parts) >= 4:
            return dt.datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError):
        return None
    return None


def _show_unit(unit: str, properties: list[str]) -> dict[str, str]:
    proc = _run(["systemctl", "show", unit, "-p", ",".join(properties)])
    result: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        if key:
            result[key] = value
    return result


def _render_header(now: dt.datetime, hostname: str) -> str:
    return (
        f"<h1>Operator status — {html.escape(hostname)}</h1>\n"
        f'<p class="meta">'
        f"Generated {html.escape(now.strftime('%Y-%m-%d %H:%M %Z'))} "
        f"({html.escape(now.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'))}). "
        f'<a href="/_internal/">Live dashboard →</a>'
        f'<a href="/status.json">Public /status.json →</a>'
        f"</p>\n"
    )


def _render_traffic(hours: int, bucket_hours: int, tz: dt.tzinfo, log_glob: str) -> str:
    chunked_md = humans.run_chunked(
        hours=hours, bucket_hours=bucket_hours, tz=tz, access_log_glob=log_glob
    )
    countries_md = humans.run_countries(hours=hours, tz=tz, access_log_glob=log_glob)
    subdivisions_md = humans.run_subdivisions(hours=hours, tz=tz, access_log_glob=log_glob)
    asns_md = humans.run_asns(hours=hours, tz=tz, access_log_glob=log_glob)
    paths_md = humans.run_paths(hours=hours, tz=tz, access_log_glob=log_glob)
    humans_md = humans.run_humans(hours=hours, tz=tz, access_log_glob=log_glob)
    chunked_html = markdown.markdown(chunked_md, extensions=["tables"])
    countries_html = markdown.markdown(countries_md, extensions=["tables"])
    subdivisions_html = markdown.markdown(subdivisions_md, extensions=["tables"])
    asns_html = markdown.markdown(asns_md, extensions=["tables"])
    paths_html = markdown.markdown(paths_md, extensions=["tables"])
    humans_html = markdown.markdown(humans_md, extensions=["tables"])
    return (
        f"<h2>Traffic ({hours}h)</h2>\n"
        f'<details class="collapsible">\n'
        f"<summary>Human / bot traffic — {bucket_hours}h buckets</summary>\n"
        f"{chunked_html}\n"
        f"</details>\n"
        f'<details class="collapsible">\n'
        f"<summary>Hits by country</summary>\n"
        f"{countries_html}\n"
        f"</details>\n"
        f'<details class="collapsible">\n'
        f"<summary>US states &amp; Canadian provinces</summary>\n"
        f"{subdivisions_html}\n"
        f"</details>\n"
        f'<details class="collapsible">\n'
        f"<summary>Hits by autonomous system</summary>\n"
        f"{asns_html}\n"
        f"</details>\n"
        f'<details class="collapsible">\n'
        f"<summary>Hits by URL</summary>\n"
        f"{paths_html}\n"
        f"</details>\n"
        f'<details class="collapsible">\n'
        f"<summary>Per-IP detail</summary>\n"
        f"{humans_html}\n"
        f"</details>\n"
    )


def _read_meminfo() -> dict[str, int]:
    meminfo: dict[str, int] = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            value = rest.strip().split()[0]
            try:
                meminfo[key] = int(value)
            except ValueError:
                continue
    return meminfo


def _render_disk_mem() -> str:
    df = _run(["df", "-P", "/home"])
    fields = df.stdout.strip().splitlines()[-1].split()
    # fields: filesystem 1024-blocks used available capacity mount
    total_kb, used_kb, avail_kb = int(fields[1]), int(fields[2]), int(fields[3])
    pct_used = int(fields[4].rstrip("%"))

    meminfo = _read_meminfo()
    mem_total_kb = meminfo.get("MemTotal", 0)
    mem_avail_kb = meminfo.get("MemAvailable", 0)
    swap_total_kb = meminfo.get("SwapTotal", 0)
    swap_free_kb = meminfo.get("SwapFree", 0)
    swap_used_kb = max(0, swap_total_kb - swap_free_kb)
    swap_used_pct = (swap_used_kb / swap_total_kb * 100) if swap_total_kb else 0.0

    disk_class = (
        "fail" if pct_used >= DISK_FAIL_PCT else ("warn" if pct_used >= DISK_WARN_PCT else "ok")
    )
    swap_class = (
        "warn"
        if (
            swap_total_kb > 0
            and swap_used_pct >= SWAP_USED_PCT_WARN
            and mem_avail_kb / 1024 < MEM_FREE_MB_WARN
        )
        else "ok"
    )
    mem_used_kb = mem_total_kb - mem_avail_kb

    return (
        "<h2>Disk &amp; memory</h2>\n"
        "<table>\n"
        f'<tr class="{disk_class}"><th>Disk /home</th>'
        f"<td>{_fmt_gb(used_kb)} used of {_fmt_gb(total_kb)} "
        f"({pct_used}%) — {_fmt_gb(avail_kb)} free</td></tr>\n"
        f"<tr><th>RAM</th>"
        f"<td>{_fmt_gb(mem_used_kb)} used of {_fmt_gb(mem_total_kb)} — "
        f"{_fmt_gb(mem_avail_kb)} available</td></tr>\n"
        f'<tr class="{swap_class}"><th>Swap</th>'
        f"<td>{_fmt_mb(swap_used_kb)} used of {_fmt_mb(swap_total_kb)} "
        f"({swap_used_pct:.1f}%) — {_fmt_mb(swap_free_kb)} free</td></tr>\n"
        "</table>\n"
        f'<p class="muted">Thresholds: disk WARN ≥{DISK_WARN_PCT}% / FAIL ≥{DISK_FAIL_PCT}%; '
        f"swap WARN if (used ≥{SWAP_USED_PCT_WARN}% AND MemAvailable &lt;{MEM_FREE_MB_WARN} MB).</p>\n"
    )


def _list_kayak_units() -> list[str]:
    proc = _run(
        ["systemctl", "list-units", "--type=service", "kayak-*", "--all", "--no-legend", "--plain"]
    )
    units: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if not parts or not parts[0].startswith("kayak-") or not parts[0].endswith(".service"):
            continue
        # Skip the kayak-notify-failure@<unit>.service template instances — they're
        # one-off responders to OnFailure cascades from the units already listed.
        if parts[0].startswith("kayak-notify-failure@"):
            continue
        units.append(parts[0])
    # Pipeline first, then alphabetic.
    units.sort(key=lambda u: (0 if u == "kayak-pipeline.service" else 1, u))
    return units


def _recent_errors(unit: str, since: str = "-24h", lines: int = 3) -> list[str]:
    proc = _run(
        ["journalctl", "-u", unit, f"--since={since}", "-p", "err", "-n", str(lines), "--no-pager"]
    )
    out: list[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("-- ") or not line.strip():
            continue
        out.append(line)
    return out


def _render_systemd() -> str:
    units = _list_kayak_units()
    rows: list[str] = []
    failed = 0
    properties = [
        "ActiveState",
        "Result",
        "ExecMainStatus",
        "ExecMainStartTimestamp",
        "ExecMainExitTimestamp",
    ]
    for unit in units:
        props = _show_unit(unit, properties)
        active = props.get("ActiveState", "?")
        result = props.get("Result", "?")
        exit_status = props.get("ExecMainStatus", "?")
        start_str = props.get("ExecMainStartTimestamp", "")
        exit_str = props.get("ExecMainExitTimestamp", "")
        start_dt = _parse_systemd_timestamp(start_str)
        exit_dt = _parse_systemd_timestamp(exit_str)
        duration = (
            f"{(exit_dt - start_dt).total_seconds():.1f}s"
            if start_dt and exit_dt and exit_dt > start_dt
            else "—"
        )

        # Flag color: failure result OR nonzero exit code → fail; inactive after never-ran → muted.
        klass = "ok"
        if result not in ("success", "") or exit_status not in ("0", "", "?"):
            klass = "fail"
            failed += 1

        errs = _recent_errors(unit)
        err_html = ""
        if errs:
            items = "\n".join(f"<li>{html.escape(e)}</li>" for e in errs)
            err_html = f'<ul class="error-list">\n{items}\n</ul>'

        rows.append(
            f'<tr class="{klass}">'
            f'<td class="unit-name">{html.escape(unit)}</td>'
            f"<td>{html.escape(active)}</td>"
            f"<td>{html.escape(result)}</td>"
            f"<td>{html.escape(exit_status)}</td>"
            f"<td>{html.escape(start_str) or '—'}</td>"
            f"<td>{duration}</td>"
            f"<td>{err_html or '<span class="muted">—</span>'}</td>"
            f"</tr>"
        )

    # Open-by-default when there's a failure so the operator sees details
    # without an extra click; the badge in the summary surfaces it when
    # the section is collapsed by hand later.
    open_attr = " open" if failed else ""
    badge = (
        f' <span class="badge-fail">{failed} failed</span>'
        if failed
        else f' <span class="meta">{len(units)} units OK</span>'
    )

    return (
        "<h2>systemd jobs</h2>\n"
        f'<details class="collapsible"{open_attr}>\n'
        f"<summary>kayak-*.service{badge}</summary>\n"
        "<table>\n"
        "<thead><tr><th>Unit</th><th>Active</th><th>Result</th><th>Exit</th>"
        "<th>Last start</th><th>Last duration</th><th>Recent errors (24h)</th></tr></thead>\n"
        "<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>\n"
        "</details>\n"
    )


def _newest_mtime(pattern: str) -> float | None:
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    return max(os.path.getmtime(f) for f in files)


def _cert_not_after(host: str, port: int = 443) -> dt.datetime | None:
    s_client = _run(
        [
            "openssl",
            "s_client",
            "-servername",
            host,
            "-connect",
            f"{host}:{port}",
        ],
        timeout=10,
    )
    if s_client.returncode != 0 or not s_client.stdout:
        return None
    x509 = _run(["openssl", "x509", "-enddate", "-noout"], stdin_text=s_client.stdout)
    line = x509.stdout.strip()
    if not line.startswith("notAfter="):
        return None
    raw = line[len("notAfter=") :]
    try:
        return dt.datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def _render_backups_cert(now: dt.datetime) -> str:
    now_ts = now.timestamp()

    def _age_row(label: str, mtime: float | None, warn_s: float) -> str:
        if mtime is None:
            return f'<tr class="fail"><th>{html.escape(label)}</th><td>no files found</td></tr>'
        age = now_ts - mtime
        klass = "warn" if age > warn_s else "ok"
        when = dt.datetime.fromtimestamp(mtime, tz=dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f'<tr class="{klass}"><th>{html.escape(label)}</th>'
            f"<td>{_fmt_age(age)} ago — {when}</td></tr>"
        )

    hourly = _newest_mtime("/home/pat/backups/hourly-*.db.gz")
    weekly = _newest_mtime("/home/pat/backups/backup-*.db.gz")

    offsite_props = _show_unit("kayak-backup-offsite.service", ["ExecMainStartTimestamp", "Result"])
    offsite_dt = _parse_systemd_timestamp(offsite_props.get("ExecMainStartTimestamp", ""))
    offsite_result = offsite_props.get("Result", "?")
    if offsite_dt is None:
        offsite_row = (
            '<tr class="warn"><th>Offsite backup (rclone → gdrive-crypt:)</th>'
            f"<td>no recent run recorded in journald (last result={html.escape(offsite_result)})"
            "</td></tr>"
        )
    else:
        offsite_mtime = offsite_dt.timestamp()
        offsite_row = _age_row(
            f"Offsite backup (rclone, last result={offsite_result})",
            offsite_mtime,
            OFFSITE_WARN_S,
        )

    cert_dt = _cert_not_after("levels.wkcc.org")
    if cert_dt is None:
        cert_row = (
            '<tr class="fail"><th>TLS cert (levels.wkcc.org)</th>'
            "<td>could not fetch via s_client</td></tr>"
        )
    else:
        days_left = (cert_dt - now.astimezone(dt.UTC)).total_seconds() / 86400
        klass = "fail" if days_left < 7 else ("warn" if days_left < CERT_WARN_DAYS else "ok")
        cert_row = (
            f'<tr class="{klass}"><th>TLS cert (levels.wkcc.org)</th>'
            f"<td>{days_left:.1f} days remaining — expires "
            f"{cert_dt.strftime('%Y-%m-%d %H:%M UTC')}</td></tr>"
        )

    return (
        "<h2>Backups &amp; cert</h2>\n"
        "<table>\n"
        + _age_row("Hourly backup (~/backups/hourly-*.db.gz)", hourly, HOURLY_WARN_S)
        + "\n"
        + _age_row("Weekly backup (~/backups/backup-*.db.gz)", weekly, WEEKLY_WARN_S)
        + "\n"
        + offsite_row
        + "\n"
        + cert_row
        + "\n</table>\n"
    )


def _render_page(
    now: dt.datetime, hours: int, bucket_hours: int, tz: dt.tzinfo, log_glob: str
) -> str:
    hostname = socket.gethostname()
    sections = [
        _render_header(now, hostname),
        _render_traffic(hours, bucket_hours, tz, log_glob),
        _render_disk_mem(),
        _render_systemd(),
        _render_backups_cert(now),
    ]
    body = "\n".join(sections)
    # Cache-bust the JS — nginx serves /static/ as immutable max-age=1y, so
    # without a ?v= the browser pins the first download forever and silently
    # keeps a stale selector after we ship a fix. mtime is monotonic across
    # deploys so it does the job without a content-hash pipeline.
    js_path = Path("/home/pat/public_html/static/internal-sort.js")
    js_v = int(js_path.stat().st_mtime) if js_path.exists() else 0
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Operator status — {html.escape(hostname)}</title>\n"
        f"<style>{_INLINE_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        f'<script src="/static/internal-sort.js?v={js_v}" defer></script>\n'
        "</body>\n</html>\n"
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def run(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.tz)
    now = dt.datetime.now(tz)
    page = _render_page(
        now=now,
        hours=args.hours,
        bucket_hours=args.bucket_hours,
        tz=tz,
        log_glob=args.log_glob,
    )
    _atomic_write(Path(args.output), page)
    return 0


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "status",
        help="Render the operator status page to HTML (nightly via kayak-status.timer)",
    )
    parser.add_argument(
        "--output",
        default=_DEFAULT_OUTPUT,
        help=f"Output path (default {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--hours", type=int, default=24, help="Traffic window in hours (default 24)"
    )
    parser.add_argument(
        "--bucket-hours", type=int, default=4, help="Traffic bucket size in hours (default 4)"
    )
    parser.add_argument("--tz", default=_DEFAULT_TZ, help=f"IANA timezone (default {_DEFAULT_TZ})")
    parser.add_argument(
        "--log-glob",
        default=_DEFAULT_LOG_GLOB,
        help=f"Nginx access-log glob (default {_DEFAULT_LOG_GLOB})",
    )
    parser.set_defaults(func=run)
