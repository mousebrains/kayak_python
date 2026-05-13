"""Fetch USGS observations via the OGC API (api.waterdata.usgs.gov).

Queries the database for all gauges with a usgs_id, then fetches continuous
(15-minute) data from the USGS OGC API.  Each station's data is written to
the correct Source record via the gauge → gauge_source → source relationship.
"""

import argparse
import logging
import os
import time
from collections.abc import Callable

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.db.cache import update_latest, update_latest_gauge
from kayak.db.engine import get_session
from kayak.db.models import DataType, Gauge, GaugeSource, Source
from kayak.db.observations import store_observations
from kayak.db.sources import get_negative_flow_source_ids

logger = logging.getLogger(__name__)

OGC_BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"

BATCH_SIZE = 150  # sites per request


def c_to_f(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return celsius * 9.0 / 5.0 + 32.0


# Maps USGS parameter code → (DataType, optional conversion function)
PARAM_MAP: dict[str, tuple[DataType, Callable[[float], float] | None]] = {
    "00060": (DataType.flow, None),  # discharge cfs
    "00065": (DataType.gauge, None),  # gage height ft
    "00010": (DataType.temperature, c_to_f),  # temp °C → °F
    "00011": (DataType.temperature, None),  # temp °F
}


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'fetch-usgs-ogc' subcommand."""
    parser = subparsers.add_parser(
        "fetch-usgs-ogc",
        help="Fetch USGS data via the OGC API (continuous 15-min data)",
    )
    parser.set_defaults(func=fetch_usgs_ogc)
    parser.add_argument(
        "--hours",
        type=int,
        default=12,
        help="Hours of history to fetch (default: 12)",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Do not write to DB",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Sites per request (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--site",
        action="append",
        default=None,
        metavar="USGS_ID",
        help="Restrict fetch to this USGS site id; repeatable (default: all gauges with usgs_id)",
    )


def _build_site_map(session: Session, restrict_to: set[str] | None = None) -> dict[str, int]:
    """Build a mapping of usgs_id → source_id from the database.

    Joins gauge → gauge_source → source to find the source_id for each
    USGS station.  When a gauge has multiple sources, prefer the one
    whose name matches the usgs_id (the actual USGS source).

    ``restrict_to``: if set, only sites whose usgs_id is in the set are
    returned (used by ``--site`` for targeted backfills).
    """
    stmt = (
        select(Gauge.usgs_id, Source.id, Source.name)
        .join(GaugeSource, Gauge.id == GaugeSource.gauge_id)
        .join(Source, GaugeSource.source_id == Source.id)
        .where(Gauge.usgs_id.is_not(None))
    )
    if restrict_to is not None:
        stmt = stmt.where(Gauge.usgs_id.in_(restrict_to))
    rows = session.execute(stmt).all()
    result: dict[str, int] = {}
    for usgs_id, source_id, source_name in rows:
        if usgs_id not in result or source_name == usgs_id:
            result[usgs_id] = source_id
    return result


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
            wait = 2**attempt
            logger.warning("Rate limited (429), waiting %ds", wait)
            time.sleep(wait)
            continue

        if resp.status_code >= 400:
            logger.error("HTTP %d for %s", resp.status_code, url)
            return None

        return resp.json()  # type: ignore[no-any-return]

    logger.error("Gave up after rate-limit retries for %s", url)
    return None


def _extract_observation_from_feature(
    feature: dict,
    site_map: dict[str, int],
    data_type: DataType,
    convert_fn: Callable[[float], float] | None,
) -> dict[str, object] | None:
    """Parse one OGC feature into an observation row, or None if unusable."""
    from datetime import UTC, datetime

    props = feature.get("properties", {})
    mon_loc = props.get("monitoring_location_id", "")
    usgs_id = mon_loc.replace("USGS-", "")
    source_id = site_map.get(usgs_id)
    if source_id is None:
        return None

    value = props.get("value")
    timestamp = props.get("time")
    if value is None or timestamp is None:
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if convert_fn is not None:
        value = convert_fn(value)

    try:
        when = datetime.fromisoformat(timestamp)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
    except ValueError:
        logger.debug("Bad timestamp: %s", timestamp)
        return None

    return {
        "source_id": source_id,
        "data_type": data_type,
        "observed_at": when,
        "value": value,
    }


def _fetch_continuous(
    site_map: dict[str, int], api_key: str | None, hours: int, batch_size: int
) -> list[dict[str, object]]:
    """Fetch continuous (15-min) data for all sites and parameter codes.

    Performs only network I/O — no database access.  Returns a list of
    observation dicts ready for store_observations().
    """
    all_rows: list[dict[str, object]] = []
    site_ids = list(site_map.keys())

    # Only fetch 00060, 00065, 00010 (skip 00011 — sites report one or the other)
    param_codes = ["00060", "00065", "00010"]

    for param_code in param_codes:
        data_type, convert_fn = PARAM_MAP[param_code]
        param_count = 0

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

            next_url: str | None = url
            while next_url is not None:
                data = _fetch_page(next_url, api_key)
                if data is None:
                    break

                for feature in data.get("features", []):
                    row = _extract_observation_from_feature(
                        feature, site_map, data_type, convert_fn
                    )
                    if row is not None:
                        all_rows.append(row)
                        param_count += 1

                # Follow pagination
                next_url = None
                for link in data.get("links", []):
                    if link.get("rel") == "next":
                        next_url = link.get("href")
                        break

        logger.info(
            "param_code=%s (%s): fetched %d observations",
            param_code,
            data_type.value,
            param_count,
        )
        print(f"  {param_code} ({data_type.value}): {param_count} observations")

    return all_rows


def fetch_usgs_ogc(args: argparse.Namespace) -> None:
    """Fetch USGS data via the OGC API."""
    api_key = os.environ.get("USGS_API_KEY")

    hours = getattr(args, "hours", 12)
    dry_run = getattr(args, "dry_run", False)
    batch_size = getattr(args, "batch_size", BATCH_SIZE)

    if dry_run:
        print("Dry run mode — no data will be stored")

    sites_arg = getattr(args, "site", None)
    restrict_to: set[str] | None = set(sites_arg) if sites_arg else None

    # Phase 1: Read site map (short read-only session)
    session = get_session()
    try:
        site_map = _build_site_map(session, restrict_to)
    finally:
        session.close()

    print(f"Found {len(site_map)} USGS sites in database")
    if not site_map:
        print("No USGS sites found — nothing to fetch")
        return

    # Phase 2: Fetch all data from API (no DB session held)
    print(f"Fetching {hours}h of continuous data...")
    all_rows = _fetch_continuous(site_map, api_key, hours, batch_size)

    if dry_run:
        print(f"Dry run: {len(all_rows)} observations fetched")
        return

    # Phase 3: Store to DB (short write session)
    if not all_rows:
        print("No observations to store")
        return

    session = get_session()
    try:
        neg_flow_sources = get_negative_flow_source_ids(session)
        stored = store_observations(session, all_rows, allow_negative_flow_sources=neg_flow_sources)
        updated_pairs = {(row["source_id"], row["data_type"]) for row in all_rows}
        print(f"Updating latest observations for {len(updated_pairs)} source/type pairs...")
        _update_latest_and_gauge_cache(session, updated_pairs)
        session.commit()
        print(f"Committed {stored} observations to database")
    finally:
        session.close()


def _update_latest_and_gauge_cache(
    session: Session,
    updated_pairs: set[tuple[object, object]],
) -> None:
    """Refresh source-level and gauge-level latest-observation caches.

    `updated_pairs` keys come from `dict[str, object]` rows so the entries
    are statically (object, object); the isinstance guards narrow them
    safely for the update_latest / update_latest_gauge calls.
    """
    source_to_gauge: dict[int, int] = {}
    for gs in session.scalars(select(GaugeSource)):
        source_to_gauge[gs.source_id] = gs.gauge_id
    for sid, dtype in updated_pairs:
        if not isinstance(sid, int) or not isinstance(dtype, DataType):
            logger.warning("Unexpected types: sid=%r dtype=%r, skipping", sid, dtype)
            continue
        update_latest(session, sid, dtype)
    gauge_pairs: set[tuple[int, DataType]] = set()
    for sid, dtype in updated_pairs:
        if isinstance(sid, int) and sid in source_to_gauge and isinstance(dtype, DataType):
            gauge_pairs.add((source_to_gauge[sid], dtype))
    for gid, dtype in gauge_pairs:
        update_latest_gauge(session, gid, dtype)
