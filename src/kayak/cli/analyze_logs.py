"""``levels analyze-logs`` — operator log analytics.

Three sub-sub-commands:

- ``release`` — full release post-mortem comparing baseline vs
  post-release windows across 9 signal sources.
- ``humans`` — distinct human visitors over a window (default 48h).
- ``chunked`` — humans-vs-bots in N-hour buckets across the window.

All three emit Markdown to stdout. See
``docs/PLAN_logs_analyze_migration.md`` for the migration history.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from zoneinfo import ZoneInfo

from kayak.analytics import humans, release_postmortem
from kayak.analytics._release_context import current_release_link, infer_release_time

_DEFAULT_TZ = "America/Los_Angeles"
_DEFAULT_LOG_GLOB = "/var/log/nginx/*access.log*"


def _parse_release(value: str | None, tz: dt.tzinfo) -> dt.datetime | None:
    if value is None:
        return None
    try:
        ts = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=tz)
    return ts


def _cmd_release(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.tz)
    release = _parse_release(args.release, tz)
    if release is None:
        release = infer_release_time(tz=tz)
    if release is None:
        print(
            f"ERROR: could not infer release time from the {current_release_link()} "
            "release pointer; pass --release explicitly",
            file=sys.stderr,
        )
        return 2

    baseline_lo = release - dt.timedelta(hours=args.baseline_hours)
    baseline = (baseline_lo, release)
    if args.window_hours > 0:
        post_hi = release + dt.timedelta(hours=args.window_hours)
    else:
        post_hi = dt.datetime.now(tz)
    post = (release, post_hi)

    report = release_postmortem.run_postmortem(
        release=release,
        baseline=baseline,
        post=post,
        tz=tz,
        access_log_glob=args.log_glob,
    )
    sys.stdout.write(report)
    return 0


def _cmd_humans(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.tz)
    report = humans.run_humans(hours=args.hours, tz=tz, access_log_glob=args.log_glob)
    sys.stdout.write(report)
    return 0


def _cmd_chunked(args: argparse.Namespace) -> int:
    tz = ZoneInfo(args.tz)
    report = humans.run_chunked(
        hours=args.hours,
        bucket_hours=args.bucket_hours,
        tz=tz,
        access_log_glob=args.log_glob,
    )
    sys.stdout.write(report)
    return 0


def _build_shared_parent() -> argparse.ArgumentParser:
    """Shared flags across release/humans/chunked sub-sub-commands."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--tz",
        default=_DEFAULT_TZ,
        help=f"IANA timezone for windows (default {_DEFAULT_TZ})",
    )
    p.add_argument(
        "--log-glob",
        default=_DEFAULT_LOG_GLOB,
        help=(
            f"Access-log glob (default {_DEFAULT_LOG_GLOB} — all kayak "
            f"vhosts). Narrow to one vhost with e.g. "
            f"/var/log/nginx/kayak-access.log*"
        ),
    )
    return p


def addArgs(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``levels analyze-logs`` + its sub-sub-commands."""
    parent = _build_shared_parent()

    parser = subparsers.add_parser(
        "analyze-logs",
        help="Operator log analytics (release post-mortem + traffic breakdowns)",
    )
    subsub = parser.add_subparsers(dest="analyze_subcommand", required=True)

    # release
    p_rel = subsub.add_parser(
        "release",
        parents=[parent],
        help="Full release post-mortem (baseline vs post-release across 9 signals)",
    )
    p_rel.add_argument(
        "--release",
        default=None,
        help=("Release timestamp ISO8601 (default: mtime of the /opt/kayak/current pointer)"),
    )
    p_rel.add_argument(
        "--baseline-hours",
        type=float,
        default=48.0,
        help="Baseline window length in hours (default 48)",
    )
    p_rel.add_argument(
        "--window-hours",
        type=float,
        default=0.0,
        help="Post-release window in hours (0 = release → now)",
    )
    p_rel.set_defaults(func=_cmd_release)

    # humans
    p_hum = subsub.add_parser(
        "humans",
        parents=[parent],
        help="Distinct human visitors over a window (with per-IP detail)",
    )
    p_hum.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Window length in hours (default 48)",
    )
    p_hum.set_defaults(func=_cmd_humans)

    # chunked
    p_chk = subsub.add_parser(
        "chunked",
        parents=[parent],
        help="N-hour-bucketed human/bot/other breakdown",
    )
    p_chk.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Window length in hours (default 48)",
    )
    p_chk.add_argument(
        "--bucket-hours",
        type=int,
        default=2,
        help="Bucket size in hours (default 2)",
    )
    p_chk.set_defaults(func=_cmd_chunked)
