#!/usr/bin/env python3
"""Seed up to 90 days of USGS observations into a freshly-split USGS source.

Companion to scripts/split_usgs_sources.py — that script ensures every
gauge with a usgs_id has a source named with the digit station ID. This
script fills that source with historical data from NWIS IV so plots have
continuity from day one rather than a hard cutover.

Source of truth: https://waterservices.usgs.gov/nwis/iv/  with format=json,
period=P90D, parameterCd=00060,00065,00010 (discharge, gage height,
temperature). Inserts go through kayak.db.observations.store_observations
which upserts via the existing primary key — re-running is safe.

Usage:
    /home/pat/.venv/bin/python3 scripts/backfill_usgs_nwis.py
    /home/pat/.venv/bin/python3 scripts/backfill_usgs_nwis.py --apply
    /home/pat/.venv/bin/python3 scripts/backfill_usgs_nwis.py --gauge-id 140 --apply
    /home/pat/.venv/bin/python3 scripts/backfill_usgs_nwis.py --period P30D --apply
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Repo src/ on path so we can import kayak.* without an editable install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from kayak.db.cache import update_latest, update_latest_gauge
from kayak.db.engine import get_session
from kayak.db.models import DataType, Gauge, GaugeSource, Source
from kayak.db.observations import store_observations
from kayak.db.sources import get_negative_flow_source_ids

logger = logging.getLogger(__name__)

NWIS_BASE = "https://waterservices.usgs.gov/nwis/iv/"
THROTTLE_S = 0.5  # between gauges
RETRY_BACKOFF = (1, 2, 4)  # seconds; len() = retries

# USGS parameter code → (DataType, optional unit converter cfs/ft/F).
# Mirrors src/kayak/cli/fetch_usgs_ogc.py:37 PARAM_MAP.
PARAM_MAP = {
    "00060": (DataType.flow, lambda v: v),
    "00065": (DataType.gauge, lambda v: v),
    "00010": (DataType.temperature, lambda c: c * 9.0 / 5.0 + 32.0),
    "00011": (DataType.temperature, lambda v: v),
}


def fetch_nwis(usgs_id: str, period: str) -> dict | None:
    """Hit NWIS IV with retries. Return parsed JSON dict or None on failure."""
    params = {
        "format": "json",
        "sites": usgs_id,
        "period": period,
        "parameterCd": "00060,00065,00010",
    }
    last_exc: Exception | None = None
    for backoff in (0, *RETRY_BACKOFF):
        if backoff:
            time.sleep(backoff)
        try:
            r = requests.get(NWIS_BASE, params=params, timeout=60)
        except requests.RequestException as exc:
            last_exc = exc
            continue
        if r.status_code == 404:
            # NWIS returns 404 when a site has no values in the window.
            return {"value": {"timeSeries": []}}
        if r.status_code >= 500 or r.status_code == 429:
            last_exc = RuntimeError(f"HTTP {r.status_code}")
            continue
        if r.status_code >= 400:
            print(f"  HTTP {r.status_code} — giving up on {usgs_id}", file=sys.stderr)
            return None
        try:
            return r.json()
        except ValueError as exc:
            last_exc = exc
            continue
    print(f"  retries exhausted for {usgs_id}: {last_exc}", file=sys.stderr)
    return None


def build_rows(payload: dict, source_id: int) -> list[dict]:
    """Translate NWIS IV JSON into store_observations() input rows."""
    rows: list[dict] = []
    for series in payload.get("value", {}).get("timeSeries", []):
        codes = series.get("variable", {}).get("variableCode", [])
        if not codes:
            continue
        param = codes[0].get("value")
        mapping = PARAM_MAP.get(param)
        if mapping is None:
            continue
        dtype, convert = mapping
        for v in series.get("values", [{}])[0].get("value", []):
            raw = v.get("value")
            ts = v.get("dateTime")
            if raw is None or ts is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            # NWIS uses -999999 for missing in some series.
            if value <= -999998:
                continue
            try:
                when = datetime.fromisoformat(ts)
            except ValueError:
                continue
            rows.append(
                {
                    "source_id": source_id,
                    "data_type": dtype,
                    "observed_at": when,
                    "value": convert(value),
                }
            )
    return rows


def usgs_source_for_gauge(session, gauge: Gauge) -> Source | None:
    """Return the source on `gauge` whose name == gauge.usgs_id, or None."""
    stmt = (
        select(Source)
        .join(GaugeSource, GaugeSource.source_id == Source.id)
        .where(GaugeSource.gauge_id == gauge.id, Source.name == gauge.usgs_id)
    )
    return session.scalars(stmt).first()


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Commit observations and refresh caches.")
    ap.add_argument("--gauge-id", type=int, default=None, help="Limit to a single gauge id.")
    ap.add_argument(
        "--period",
        default="P90D",
        help="ISO 8601 duration sent to NWIS as period= (default: P90D).",
    )
    args = ap.parse_args()

    session = get_session()
    try:
        stmt = select(Gauge).where(Gauge.usgs_id.is_not(None)).order_by(Gauge.id)
        if args.gauge_id is not None:
            stmt = stmt.where(Gauge.id == args.gauge_id)
        gauges = list(session.scalars(stmt))
        if not gauges:
            print("No gauges matched.")
            return 0

        neg_flow_sources = get_negative_flow_source_ids(session) if args.apply else set()

        total_rows = 0
        skipped_no_source = 0
        skipped_no_data = 0
        succeeded = 0

        hdr = f"{'gauge_id':>8}  {'usgs_id':<10}  {'src_id':>6}  {'rows':>7}  {'flow/gauge/temp':<20}  status"
        print(hdr)
        print("-" * len(hdr))

        for g in gauges:
            src = usgs_source_for_gauge(session, g)
            if src is None:
                print(
                    f"{g.id:>8}  {g.usgs_id or '':<10}  {'-':>6}  {'-':>7}  {'':<20}  no usgs-named source (run split_usgs_sources first)"
                )
                skipped_no_source += 1
                continue

            payload = fetch_nwis(g.usgs_id, args.period)
            if payload is None:
                print(f"{g.id:>8}  {g.usgs_id:<10}  {src.id:>6}  {'-':>7}  {'':<20}  fetch failed")
                continue

            rows = build_rows(payload, src.id)
            if not rows:
                print(
                    f"{g.id:>8}  {g.usgs_id:<10}  {src.id:>6}  {0:>7}  {'':<20}  no data in window"
                )
                skipped_no_data += 1
                time.sleep(THROTTLE_S)
                continue

            counts = {dt.value: 0 for dt in (DataType.flow, DataType.gauge, DataType.temperature)}
            for r in rows:
                counts[r["data_type"].value] += 1
            type_summary = "/".join(str(counts[d]) for d in ("flow", "gauge", "temperature"))

            if args.apply:
                stored = store_observations(
                    session, rows, allow_negative_flow_sources=neg_flow_sources
                )
                seen_dtypes = {r["data_type"] for r in rows}
                for dt in seen_dtypes:
                    update_latest(session, src.id, dt)
                    update_latest_gauge(session, g.id, dt)
                session.commit()
                total_rows += stored
                succeeded += 1
                print(
                    f"{g.id:>8}  {g.usgs_id:<10}  {src.id:>6}  {stored:>7}  {type_summary:<20}  committed"
                )
            else:
                total_rows += len(rows)
                print(
                    f"{g.id:>8}  {g.usgs_id:<10}  {src.id:>6}  {len(rows):>7}  {type_summary:<20}  dry-run"
                )

            time.sleep(THROTTLE_S)

        print()
        print(
            f"Totals: gauges={len(gauges)}  succeeded={succeeded}  "
            f"no_source={skipped_no_source}  no_data={skipped_no_data}  "
            f"observations={'committed=' if args.apply else 'would_insert='}{total_rows}"
        )
        if not args.apply:
            print("Dry run — pass --apply to commit.")

        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
