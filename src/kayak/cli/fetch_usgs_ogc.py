"""Fetch USGS observations via the OGC API (api.waterdata.usgs.gov).

Queries the database for all gauges with a usgs_id, then fetches continuous
(15-minute) data from the USGS OGC API.  Each station's data is written to
the correct Source record via the gauge → gauge_source → source relationship.
"""

from __future__ import annotations

import logging
import os
import time

import requests
from sqlalchemy import select

from kayak.db.data_db import store_observation, update_latest
from kayak.db.engine import get_session
from kayak.db.models import DataType, Gauge, GaugeSource, Source

logger = logging.getLogger(__name__)

OGC_BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"

BATCH_SIZE = 150  # sites per request


def c_to_f(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return celsius * 9.0 / 5.0 + 32.0


# Maps USGS parameter code → (DataType, optional conversion function)
PARAM_MAP: dict[str, tuple[DataType, object]] = {
    "00060": (DataType.flow, None),           # discharge cfs
    "00065": (DataType.gauge, None),          # gage height ft
    "00010": (DataType.temperature, c_to_f),  # temp °C → °F
    "00011": (DataType.temperature, None),    # temp °F
}


def addArgs(subparsers):
    """Register the 'fetch-usgs-ogc' subcommand."""
    parser = subparsers.add_parser(
        "fetch-usgs-ogc",
        help="Fetch USGS data via the OGC API (continuous 15-min data)",
    )
    parser.set_defaults(func=fetch_usgs_ogc)
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Hours of history to fetch (default: 24)",
    )
    parser.add_argument(
        "-d", "--dry-run", action="store_true",
        help="Do not write to DB",
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"Sites per request (default: {BATCH_SIZE})",
    )


def _build_site_map(session) -> dict[str, int]:
    """Build a mapping of usgs_id → source_id from the database.

    Joins gauge → gauge_source → source to find the source_id for each
    USGS station.
    """
    rows = session.execute(
        select(Gauge.usgs_id, Source.id)
        .join(GaugeSource, Gauge.id == GaugeSource.gauge_id)
        .join(Source, GaugeSource.source_id == Source.id)
        .where(Gauge.usgs_id.is_not(None))
    ).all()
    return {usgs_id: source_id for usgs_id, source_id in rows}


def _fetch_page(url: str, api_key: str | None) -> dict | None:
    """Fetch a single page from the OGC API, returning parsed JSON.

    Retries with exponential backoff on 429 (rate limit) responses.
    Returns None on failure.
    """
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    for attempt in range(4):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as exc:
            logger.error("Request failed for %s: %s", url, exc)
            return None

        if resp.status_code == 429:
            wait = 2 ** attempt
            logger.warning("Rate limited (429), waiting %ds", wait)
            time.sleep(wait)
            continue

        if resp.status_code >= 400:
            logger.error("HTTP %d for %s", resp.status_code, url)
            return None

        return resp.json()

    logger.error("Gave up after rate-limit retries for %s", url)
    return None


def _fetch_continuous(session, site_map, api_key, hours, batch_size, dry_run):
    """Fetch continuous (15-min) data for all sites and parameter codes.

    Returns a set of (source_id, DataType) pairs that received new data,
    so the caller can update latest observations.
    """
    updated_pairs: set[tuple[int, DataType]] = set()
    site_ids = list(site_map.keys())

    # Only fetch 00060, 00065, 00010 (skip 00011 — sites report one or the other)
    param_codes = ["00060", "00065", "00010"]

    for param_code in param_codes:
        data_type, convert_fn = PARAM_MAP[param_code]
        total_stored = 0

        for i in range(0, len(site_ids), batch_size):
            batch = site_ids[i : i + batch_size]
            cql_ids = ",".join(f"'USGS-{sid}'" for sid in batch)
            cql_filter = f"monitoring_location_id IN ({cql_ids})"

            url = (
                f"{OGC_BASE}/collections/continuous/items"
                f"?f=json&limit=10000"
                f"&parameter_code={param_code}"
                f"&time=PT{hours}H"
                f"&filter={cql_filter}"
                f"&filter-lang=cql-text"
            )

            page_num = 0
            while url:
                page_num += 1
                data = _fetch_page(url, api_key)
                if data is None:
                    break

                features = data.get("features", [])
                for feature in features:
                    props = feature.get("properties", {})
                    mon_loc = props.get("monitoring_location_id", "")
                    usgs_id = mon_loc.replace("USGS-", "")

                    source_id = site_map.get(usgs_id)
                    if source_id is None:
                        continue

                    value = props.get("value")
                    timestamp = props.get("time")
                    if value is None or timestamp is None:
                        continue

                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        continue

                    if convert_fn is not None:
                        value = convert_fn(value)

                    from datetime import UTC, datetime

                    try:
                        when = datetime.fromisoformat(timestamp)
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=UTC)
                    except ValueError:
                        logger.debug("Bad timestamp: %s", timestamp)
                        continue

                    if not dry_run:
                        if store_observation(session, source_id, data_type, when, value):
                            updated_pairs.add((source_id, data_type))
                            total_stored += 1
                    else:
                        total_stored += 1

                # Follow pagination
                url = None
                for link in data.get("links", []):
                    if link.get("rel") == "next":
                        url = link.get("href")
                        break

        logger.info(
            "param_code=%s (%s): stored %d observations",
            param_code, data_type.value, total_stored,
        )
        print(f"  {param_code} ({data_type.value}): {total_stored} observations")

    return updated_pairs


def fetch_usgs_ogc(args):
    """Fetch USGS data via the OGC API."""
    api_key = os.environ.get("USGS_API_KEY")
    if not api_key:
        logger.warning("USGS_API_KEY not set — skipping OGC fetch")
        print("USGS_API_KEY not set — skipping OGC fetch")
        return

    hours = getattr(args, "hours", 24)
    dry_run = getattr(args, "dry_run", False)
    batch_size = getattr(args, "batch_size", BATCH_SIZE)

    if dry_run:
        print("Dry run mode — no data will be stored")

    session = get_session()
    try:
        site_map = _build_site_map(session)
        print(f"Found {len(site_map)} USGS sites in database")

        if not site_map:
            print("No USGS sites found — nothing to fetch")
            return

        print(f"Fetching {hours}h of continuous data...")
        updated = _fetch_continuous(
            session, site_map, api_key, hours, batch_size, dry_run,
        )

        if not dry_run:
            print(f"Updating latest observations for {len(updated)} source/type pairs...")
            for source_id, data_type in updated:
                update_latest(session, source_id, data_type)
            session.commit()
            print("Committed to database")
        else:
            session.rollback()

    finally:
        session.close()
