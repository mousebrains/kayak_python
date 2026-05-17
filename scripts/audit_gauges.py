#!/usr/bin/env python3
"""Audit gauge metadata: refresh caches, find candidates, detect data changes.

Refreshes the USGS and NWPS gauge metadata caches, then compares against the
kayak database to find:
  - New gauges near existing reaches that aren't linked to any gauge
  - New gauges on rivers that have reaches in the DB
  - Gauges that stopped providing data in the last week
  - Gauges that started providing data in the last week

Advisory only — this script never deletes gauges. Gauges without a linked
reach are first-class (a few feed calc expressions, others are kept for
historical/manual-merge use) and are never recommended for removal on that
basis. Any cleanup tool that wants to delete a gauge must use
``kayak.db.gauges.delete_gauge``, which enforces the safety chokepoint.

Usage:
    python3 scripts/audit_gauges.py [--no-refresh] [--days 7]
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Reuse the existing fetch scripts
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

CACHE_DB = SCRIPT_DIR.parent / "Gauge-metadata-cache" / "gauges.db"
KAYAK_DB = Path.home() / "DB" / "kayak.db"


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def refresh_caches():
    """Re-run the USGS and NWPS site fetch scripts."""
    print("=" * 60)
    print("Refreshing gauge metadata caches")
    print("=" * 60)

    from fetch_nwps_sites import main as fetch_nwps
    from fetch_usgs_sites import main as fetch_usgs

    saved_argv = sys.argv
    sys.argv = [sys.argv[0], str(CACHE_DB)]
    print("\n--- USGS sites ---")
    fetch_usgs()
    print("\n--- NWPS sites ---")
    fetch_nwps()
    sys.argv = saved_argv


def find_new_usgs_gauges(cache, kayak, active_only=True):
    """Find USGS gauges in the cache that aren't in the kayak DB."""
    # All USGS IDs currently in kayak
    known = set(
        r[0]
        for r in kayak.execute("SELECT usgs_id FROM gauge WHERE usgs_id IS NOT NULL").fetchall()
    )

    if active_only:
        # Only include sites with flow or gage data in the last 30 days
        new = cache.execute(
            "SELECT site_no, station_nm, latitude, longitude, "
            "drain_area_sq_mi, huc_cd FROM usgs_site "
            "WHERE (last_flow_date > date('now', '-30 days') "
            "    OR last_gage_date > date('now', '-30 days'))"
        ).fetchall()
    else:
        new = cache.execute(
            "SELECT site_no, station_nm, latitude, longitude, "
            "drain_area_sq_mi, huc_cd FROM usgs_site"
        ).fetchall()

    return [r for r in new if r[0] not in known]


def find_new_nwps_gauges(cache, kayak):
    """Find NWPS gauges in the cache that aren't in the kayak DB."""
    known = set()
    for col in ["nws_id", "nwsli_id", "cbtt_id"]:
        rows = kayak.execute(f"SELECT {col} FROM gauge WHERE {col} IS NOT NULL").fetchall()
        known.update(r[0] for r in rows)

    # Also check source names
    src_names = set(r[0] for r in kayak.execute("SELECT name FROM source").fetchall())
    known.update(src_names)

    new = cache.execute("SELECT lid, name, latitude, longitude, state FROM nwps_site").fetchall()

    return [r for r in new if r[0] not in known]


def load_audit_ignore(path: Path | None = None) -> set[tuple[str, str, int]]:
    """Load the (kind, gauge_id, reach_id) tuples to suppress from candidates.

    See ``data/audit_ignore.yaml`` for the schema. Missing file is fine —
    returns an empty set so the audit runs clean before any entries exist.
    """
    if path is None:
        path = SCRIPT_DIR.parent / "data" / "audit_ignore.yaml"
    if not path.is_file():
        return set()
    import yaml  # local import — only this code path needs PyYAML

    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    out: set[tuple[str, str, int]] = set()
    for entry in doc.get("ignored_candidates", []) or []:
        kind = str(entry.get("kind", "")).upper()
        gid = str(entry.get("gauge_id", ""))
        rid = entry.get("reach_id")
        if kind in ("USGS", "NWPS") and gid and isinstance(rid, int):
            out.add((kind, gid, rid))
    return out


def find_candidates_near_reaches(
    new_gauges,
    kayak,
    max_dist_miles=15,
    kind: str = "USGS",
    ignore: set[tuple[str, str, int]] | None = None,
):
    """Find new gauges near reaches that have no gauge or a distant gauge.

    ``kind`` is "USGS" or "NWPS" — used to key into ``ignore``, which
    suppresses specific (kind, gauge_id, reach_id) pairs marked as
    not-actually-useful in ``data/audit_ignore.yaml``. Suppression
    happens before the per-gauge dedup so a gauge that's wrong for the
    closest reach can still surface against a more-distant reach where
    it'd actually fit.
    """
    ignore = ignore or set()
    reaches = kayak.execute("""
        SELECT r.id, r.display_name, r.name, r.river, r.gauge_id,
               r.latitude_start, r.longitude_start,
               r.latitude_end, r.longitude_end
        FROM reach r
        WHERE r.no_show = 0
          AND r.latitude_start IS NOT NULL
    """).fetchall()

    candidates = []
    for gauge in new_gauges:
        if len(gauge) == 6:
            # USGS: site_no, name, lat, lon, drain_area, huc
            gid, gname, glat, glon = gauge[0], gauge[1], gauge[2], gauge[3]
        else:
            # NWPS: lid, name, lat, lon, state
            gid, gname, glat, glon = gauge[0], gauge[1], gauge[2], gauge[3]

        if glat is None or glon is None:
            continue

        for reach in reaches:
            rid, dname, rname, _river, rgauge, slat, slon, elat, elon = reach
            label = dname or rname

            if slat is None or elat is None or slon is None or elon is None:
                continue
            if (kind, str(gid), rid) in ignore:
                continue

            # Distance to midpoint of reach
            mid_lat = (slat + elat) / 2
            mid_lon = (slon + elon) / 2
            dist = haversine_miles(glat, glon, mid_lat, mid_lon)

            if dist <= max_dist_miles:
                has_gauge = "yes" if rgauge else "NO"
                candidates.append((dist, gid, gname, rid, label, has_gauge))

    # Sort by distance, deduplicate by gauge
    candidates.sort()
    seen = set()
    unique = []
    for c in candidates:
        if c[1] not in seen:
            seen.add(c[1])
            unique.append(c)

    return unique


def check_data_status(kayak, days=7):
    """Check for gauges that stopped or started providing flow data."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    week_ago = (datetime.now(UTC) - timedelta(days=days * 2)).strftime("%Y-%m-%d %H:%M:%S")

    # Gauges that had flow obs in the past 2*N day window but nothing since
    # cutoff. The WHERE only filters out obs older than 2*N days so that
    # HAVING max < cutoff truthfully reflects "no data since cutoff" — the
    # earlier version filtered the WHERE clause to < cutoff as well, which
    # made HAVING trivially true and listed every gauge with any obs in the
    # window regardless of newer data.
    #
    # The HAVING also requires gauge-height to be stale: USGS routinely
    # stops publishing flow when the rating table is deemed unreliable
    # (e.g. Willamette at upper falls, USGS 14207740 after 2026-04-20)
    # while the underlying gauge-height feed keeps working. That's not a
    # broken feed — the gauge is still measurable, just not rated — so
    # we only flag STOPPED when both flow AND gauge data have died.
    stopped = kayak.execute(
        """
        SELECT g.id, g.name, g.usgs_id,
               max(CASE WHEN o.data_type='flow'  THEN o.observed_at END) AS last_flow,
               max(CASE WHEN o.data_type='gauge' THEN o.observed_at END) AS last_gauge,
               count(*) FILTER (WHERE o.data_type='flow') AS obs_count
        FROM gauge g
        JOIN gauge_source gs ON gs.gauge_id = g.id
        JOIN source s ON gs.source_id = s.id
        JOIN observation o ON o.source_id = s.id
        WHERE o.data_type IN ('flow', 'gauge')
          AND o.observed_at > ?
        GROUP BY g.id
        HAVING max(CASE WHEN o.data_type='flow'  THEN o.observed_at END) < ?
           AND (max(CASE WHEN o.data_type='gauge' THEN o.observed_at END) IS NULL
                OR max(CASE WHEN o.data_type='gauge' THEN o.observed_at END) < ?)
    """,
        (week_ago, cutoff_str, cutoff_str),
    ).fetchall()
    # Output shape preserved (id, name, usgs_id, last_obs, obs_count) so the
    # caller (text + JSON renderers, _group_stale_by_gauge consumers) is
    # unchanged. last_obs is the flow timestamp since that's what STOPPED
    # is fundamentally about.
    stopped = [
        (gid, gname, usgs_id, last_flow, count)
        for gid, gname, usgs_id, last_flow, _last_gauge, count in stopped
    ]

    # Gauges with flow obs since cutoff but none in the prior N-day window.
    # WHERE looks back 2*N days so the HAVING min > cutoff actually rules
    # out a sibling source carrying pre-cutoff data; the prior `> cutoff`
    # filter made the HAVING trivially true.
    started = kayak.execute(
        """
        SELECT g.id, g.name, g.usgs_id,
               min(o.observed_at) AS first_obs,
               count(*) AS obs_count
        FROM gauge g
        JOIN gauge_source gs ON gs.gauge_id = g.id
        JOIN source s ON gs.source_id = s.id
        JOIN observation o ON o.source_id = s.id
        WHERE o.data_type = 'flow'
          AND o.observed_at > ?
        GROUP BY g.id
        HAVING min(o.observed_at) > ?
    """,
        (week_ago, cutoff_str),
    ).fetchall()

    # Reach-linked gauges where NEITHER flow NOR gauge data has been recent
    # on ANY of the gauge's linked sources. Aggregating before the staleness
    # filter (HAVING, not WHERE) avoids the per-source-row trap where a single
    # flow-less source flagged the whole gauge despite a sibling source serving
    # data fine.
    stale = kayak.execute(
        """
        SELECT g.id, g.name, g.usgs_id,
               r.id AS reach_id, r.display_name AS reach_name,
               MAX(lo.observed_at) AS last_obs,
               'flow_or_gauge' AS data_type
        FROM reach r
        JOIN gauge g ON r.gauge_id = g.id
        JOIN gauge_source gs ON gs.gauge_id = g.id
        JOIN source s ON gs.source_id = s.id
        LEFT JOIN latest_observation lo ON lo.source_id = s.id
            AND lo.data_type IN ('flow', 'gauge')
        WHERE r.no_show = 0
        GROUP BY g.id, r.id, g.name, g.usgs_id, r.display_name
        HAVING MAX(lo.observed_at) IS NULL OR MAX(lo.observed_at) < ?
    """,
        (cutoff_str,),
    ).fetchall()

    return stopped, started, stale


def _group_stale_by_gauge(stale: list) -> list:
    """Fold the per-(reach,gauge) stale list into one entry per gauge.

    The check_data_status query returns one row per (gauge, reach) pair, so a
    gauge that drives multiple reaches appears multiple times. last_obs is
    gauge-level (same value for every row of a given gauge), so the fold is
    lossless. Output is sorted by gauge name for stable presentation.

    Returns: [(gid, gname, usgs_id, last_obs, [(rid, rname), ...]), ...]
    """
    by_gid: dict = {}
    for gid, gname, usgs_id, rid, rname, last_obs, _dtype in stale:
        if gid not in by_gid:
            by_gid[gid] = [gname, usgs_id, last_obs, []]
        by_gid[gid][3].append((rid, rname or "<unnamed>"))
    result = [
        (gid, gname, usgs_id, last_obs, sorted(reaches))
        for gid, (gname, usgs_id, last_obs, reaches) in by_gid.items()
    ]
    result.sort(key=lambda r: (r[1] or "", r[0]))
    return result


def _send_email_digest(  # noqa: C901 — pre-existing complexity; tracked in task #45 refactor
    addr: str,
    days: int,
    stopped: list,
    started: list,
    stale: list,
    usgs_candidates: list,
    nwps_candidates: list,
) -> None:
    """Mail a digest to addr. Always sends; subject conveys urgency.

    Failures are logged to stderr but do not raise — a broken mail pipeline
    should not turn a successful audit into a unit failure (which would
    trigger OnFailure= and email a different alert).
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    candidate_count = len(usgs_candidates) + len(nwps_candidates)
    findings = bool(stopped or started or stale or candidate_count)

    if findings:
        parts = []
        if stopped:
            parts.append(f"{len(stopped)} stopped")
        if started:
            parts.append(f"{len(started)} started")
        if candidate_count:
            parts.append(f"{candidate_count} candidates")
        if stale:
            n_stale_gauges = len({row[0] for row in stale})
            parts.append(f"{n_stale_gauges} stale gauges")
        subject = f"Kayak audit {today}: " + ", ".join(parts)
    else:
        subject = f"Kayak audit {today}: clean"

    lines = [f"=== Kayak gauge audit — {days}-day window — {today} ===", ""]

    if stopped:
        lines.append(
            f"STOPPED FEEDS ({len(stopped)}) — gauges with data {days}d ago but none since"
        )
        for _gid, gname, usgs_id, last_obs, _count in stopped:
            lines.append(f"  • {gname} (USGS {usgs_id or 'N/A'}) — last obs {last_obs}")
        lines.append("")

    if stale:
        grouped = _group_stale_by_gauge(stale)
        n_reaches = sum(len(r[4]) for r in grouped)
        lines.append(
            f"STALE GAUGES ({len(grouped)}, {n_reaches} reaches affected) — "
            f"gauges with no flow or gauge data in last {days}d"
        )
        for _gid, gname, usgs_id, last_obs, reaches in grouped:
            lo = last_obs or "never"
            reaches_s = ", ".join(f"{rname} [r={rid}]" for rid, rname in reaches)
            lines.append(
                f"  • {gname} (USGS {usgs_id or 'N/A'}) — last obs {lo} — affects: {reaches_s}"
            )
        lines.append("")

    if started:
        lines.append(
            f"STARTED FEEDS ({len(started)}) — gauges with new flow data after a quiet window"
        )
        for _gid, gname, usgs_id, first_obs, count in started:
            lines.append(
                f"  • {gname} (USGS {usgs_id or 'N/A'}) — first obs {first_obs} ({count} new)"
            )
        lines.append("")

    if candidate_count:
        lines.append(f"NEW CANDIDATES near existing reaches ({candidate_count})")
        combined = [("USGS", *c) for c in usgs_candidates] + [("NWPS", *c) for c in nwps_candidates]
        combined.sort(key=lambda x: x[1])
        for kind, dist, gid, gname, _rid, rlabel, has_gauge in combined[:30]:
            tail = "  [reach already gauged]" if has_gauge == "yes" else ""
            lines.append(f'  • {kind} {gid}: {gname} — {dist:.1f} mi from reach "{rlabel}"{tail}')
        if len(combined) > 30:
            lines.append(f"  ... and {len(combined) - 30} more (full list in journal)")
        lines.append("")

    if not findings:
        lines.append("No findings in any category. All quiet.")
        lines.append("")

    body = "\n".join(lines)

    if shutil.which("mail") is None:
        print("WARNING: 'mail' not on PATH; skipping audit email", file=sys.stderr)
        return

    try:
        subprocess.run(
            ["mail", "-s", subject, addr],
            input=body.encode("utf-8"),
            check=True,
            timeout=30,
        )
        print(f"Emailed audit digest to {addr}: {subject}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        print(f"WARNING: failed to send audit email: {e}", file=sys.stderr)


def main():  # noqa: C901 — pre-existing complexity; tracked in task #45 refactor
    parser = argparse.ArgumentParser(description="Audit gauge metadata")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip refreshing the metadata caches",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Window in days for data status checks (default: 7)",
    )
    parser.add_argument(
        "--cache-db",
        type=str,
        default=str(CACHE_DB),
        help=f"Path to gauge metadata cache (default: {CACHE_DB})",
    )
    parser.add_argument(
        "--kayak-db",
        type=str,
        default=str(KAYAK_DB),
        help=f"Path to kayak database (default: {KAYAK_DB})",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=os.environ.get("AUDIT_EMAIL"),
        help="Email digest to this address (or set AUDIT_EMAIL). Always sends if set.",
    )
    args = parser.parse_args()

    if not args.no_refresh:
        refresh_caches()

    cache = sqlite3.connect(args.cache_db)
    kayak = sqlite3.connect(args.kayak_db)

    # --- New gauges ---
    print("\n" + "=" * 60)
    print("New USGS gauges not in kayak DB")
    print("=" * 60)
    new_usgs = find_new_usgs_gauges(cache, kayak)
    print(f"Found {len(new_usgs)} USGS sites not in DB")

    print("\n" + "=" * 60)
    print("New NWPS gauges not in kayak DB")
    print("=" * 60)
    new_nwps = find_new_nwps_gauges(cache, kayak)
    print(f"Found {len(new_nwps)} NWPS sites not in DB")

    # --- Candidates near reaches ---
    ignore = load_audit_ignore()
    print("\n" + "=" * 60)
    print("New USGS gauges within 15 miles of a reach")
    print("=" * 60)
    usgs_candidates = find_candidates_near_reaches(new_usgs, kayak, kind="USGS", ignore=ignore)
    if usgs_candidates:
        print(f"{'Dist':>5}  {'USGS ID':<12} {'Station':<45} {'Reach':<30} {'Gauged'}")
        print("-" * 105)
        for dist, gid, gname, _rid, rlabel, has_gauge in usgs_candidates[:30]:
            print(f"{dist:>4.1f}  {gid:<12} {gname[:45]:<45} {rlabel[:30]:<30} {has_gauge}")
        if len(usgs_candidates) > 30:
            print(f"  ... and {len(usgs_candidates) - 30} more")
    else:
        print("  None found")

    print("\n" + "=" * 60)
    print("New NWPS gauges within 15 miles of a reach")
    print("=" * 60)
    nwps_candidates = find_candidates_near_reaches(new_nwps, kayak, kind="NWPS", ignore=ignore)
    if nwps_candidates:
        print(f"{'Dist':>5}  {'LID':<12} {'Name':<45} {'Reach':<30} {'Gauged'}")
        print("-" * 105)
        for dist, gid, gname, _rid, rlabel, has_gauge in nwps_candidates[:30]:
            print(f"{dist:>4.1f}  {gid:<12} {gname[:45]:<45} {rlabel[:30]:<30} {has_gauge}")
        if len(nwps_candidates) > 30:
            print(f"  ... and {len(nwps_candidates) - 30} more")
    else:
        print("  None found")

    # --- Data status ---
    print("\n" + "=" * 60)
    print(f"Gauges that STOPPED providing flow data (last {args.days} days)")
    print("=" * 60)
    stopped, started, stale = check_data_status(kayak, args.days)
    if stopped:
        for _gid, gname, usgs_id, last_obs, _count in stopped:
            print(f"  {gname:<35} (USGS {usgs_id or 'N/A':<12}) last: {last_obs}")
    else:
        print("  None")

    print("\n" + "=" * 60)
    print(f"Gauges that STARTED providing flow data (last {args.days} days)")
    print("=" * 60)
    if started:
        for _gid, gname, usgs_id, first_obs, count in started:
            print(f"  {gname:<35} (USGS {usgs_id or 'N/A':<12}) first: {first_obs}  ({count} obs)")
    else:
        print("  None")

    print("\n" + "=" * 60)
    print(f"Stale gauges with NO flow OR gauge data in last {args.days} days")
    print("=" * 60)
    if stale:
        grouped = _group_stale_by_gauge(stale)
        for _gid, gname, usgs_id, last_obs, reaches in grouped:
            lo = last_obs or "never"
            reaches_s = ", ".join(f"{rname} [r={rid}]" for rid, rname in reaches)
            print(f"  {gname:<35} (USGS {usgs_id or 'N/A':<12}) last: {lo}")
            print(f"    affects: {reaches_s}")
    else:
        print("  None")

    cache.close()
    kayak.close()

    if args.email:
        _send_email_digest(
            args.email,
            args.days,
            stopped,
            started,
            stale,
            usgs_candidates,
            nwps_candidates,
        )

    print("\n" + "=" * 60)
    print("Audit complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
