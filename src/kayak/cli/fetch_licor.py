"""Fetch LI-COR public-dashboard timeseries data (POST) and store observations.

The shared ``levels fetch`` async client is GET-only; the LI-COR timeseries
endpoint requires a POST with a JSON body. This standalone step — a sibling of
``fetch-usgs-ogc`` — reads the active ``fetch_url`` rows whose parser uses a POST
transport (i.e. ``licor``), POSTs the request the configured URL describes, and
feeds the JSON to the registered :class:`~kayak.parsers.licor.LicorParser` so
storage, cache updates, and station attribution match every other parser.

The configured ``fetch_url.url`` is treated as declarative config, e.g.::

    https://www.licor.cloud/api/v2/timeseriesdata
        ?dashboardUUID=<uuid>
        &flow=<channel-uuid>&gauge=<channel-uuid>&temperature=<channel-uuid>
        &last=2&unit=days&interval=15&intervalUnit=minutes

``build_request`` translates that into the POST body and fails closed (before any
network I/O) on a malformed URL.
"""

import argparse
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import requests  # type: ignore[import-untyped]  # match http_client.py: optional types-requests stub
from sqlalchemy.orm import Session

from kayak.config import FETCH_TIMEOUT
from kayak.db.engine import get_session
from kayak.db.models import FetchState
from kayak.db.sources import get_active_fetch_urls
from kayak.parsers.licor import CHANNEL_PARAMS, METRIC_NAMES, LicorParser
from kayak.parsers.registry import ensure_all_loaded, get_parser_class
from kayak.utils.http_client import _validate_url

logger = logging.getLogger(__name__)

LICOR_HOST = "www.licor.cloud"
LICOR_PATH = "/api/v2/timeseriesdata"
_MAX_RETRIES = 3
_MAX_BODY_BYTES = 10_000_000  # responses are a few KB; cap defensively
_CHANNEL_LIMIT = 10000  # LI-COR max points per channel per request


@dataclass
class _LicorWork:
    """One LI-COR fetch unit prepared before network I/O."""

    url: str
    source_map: dict[str, int] = field(default_factory=dict)
    source_id: int | None = None
    fetch_url_id: int | None = None


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'fetch-licor' subcommand."""
    parser = subparsers.add_parser(
        "fetch-licor",
        help="Fetch LI-COR public-dashboard timeseries data (POST) and store observations",
    )
    parser.set_defaults(func=fetch_licor)
    parser.add_argument("-d", "--dry-run", action="store_true", help="Do not write to DB")
    parser.add_argument("-n", "--show-name", action="store_true", help="Show URLs being fetched")


def _one(params: dict[str, list[str]], key: str) -> str | None:
    """First value for a query-param key, or None."""
    values = params.get(key)
    return values[0] if values else None


def _parse_window(params: dict[str, list[str]]) -> tuple[int, str, int, str]:
    """Validate the relative-time window + aggregation interval. Raises ValueError."""
    try:
        last = int(_one(params, "last") or "2")
        interval = int(_one(params, "interval") or "15")
    except ValueError as exc:
        raise ValueError(f"LI-COR url has non-integer last/interval: {params}") from exc
    if not 1 <= last <= 7:
        raise ValueError(f"LI-COR url 'last' must be 1-7: {last}")
    if interval < 1:
        raise ValueError(f"LI-COR url 'interval' must be >= 1: {interval}")
    unit = _one(params, "unit") or "days"
    if unit not in ("days", "hours"):
        raise ValueError(f"LI-COR url 'unit' must be days/hours: {unit!r}")
    interval_unit = _one(params, "intervalUnit") or "minutes"
    if interval_unit not in ("minutes", "hours"):
        raise ValueError(f"LI-COR url 'intervalUnit' must be minutes/hours: {interval_unit!r}")
    return last, unit, interval, interval_unit


def _build_channels(params: dict[str, list[str]], interval: int, interval_unit: str) -> list[dict]:
    """One POST channel spec per configured channel UUID. Raises ValueError if any is missing."""
    channels: list[dict] = []
    for param, dtype in CHANNEL_PARAMS.items():
        uuid = _one(params, param)
        if not uuid:
            raise ValueError(f"LI-COR url missing '{param}' channel UUID")
        channels.append(
            {
                "channelUUID": uuid,
                "channelType": "dataChannel",
                "metricName": METRIC_NAMES[dtype],
                "aggregationFunction": "avg",
                "aggregationInterval": {"value": interval, "unit": interval_unit},
                "limit": _CHANNEL_LIMIT,
            }
        )
    return channels


def build_request(url: str) -> tuple[str, dict]:
    """Translate a configured LI-COR fetch_url into ``(endpoint, post_body)``.

    Raises ``ValueError`` (fail closed, before any network I/O) on a malformed
    config URL: wrong scheme/host/path, missing ``dashboardUUID``, a missing
    channel UUID, or an out-of-range window/interval.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"LI-COR url scheme not allowed: {url!r}")
    if parsed.hostname != LICOR_HOST:
        raise ValueError(f"LI-COR url host must be {LICOR_HOST}: {url!r}")
    if parsed.path != LICOR_PATH:
        raise ValueError(f"LI-COR url path must be {LICOR_PATH}: {url!r}")

    params = parse_qs(parsed.query)
    dashboard = _one(params, "dashboardUUID")
    if not dashboard:
        raise ValueError(f"LI-COR url missing dashboardUUID: {url!r}")

    last, unit, interval, interval_unit = _parse_window(params)
    channels = _build_channels(params, interval, interval_unit)

    endpoint = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
    body = {
        "channels": channels,
        "time": {"relative": {"last": last, "unit": unit}},
        "dashboardUUID": dashboard,
    }
    return endpoint, body


def _post(endpoint: str, body: dict, timeout: int) -> str | None:
    """POST the timeseries request, retrying on 429. Returns text or None."""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(
                endpoint, json=body, headers={"Accept": "application/json"}, timeout=timeout
            )
        except requests.RequestException as exc:
            logger.error("LI-COR request failed for %s: %s", endpoint, exc)
            return None

        if resp.status_code == 429:
            wait = 2**attempt
            logger.warning("LI-COR rate limited (429), waiting %ds", wait)
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            logger.error("HTTP %d from LI-COR %s", resp.status_code, endpoint)
            return None
        if len(resp.content) > _MAX_BODY_BYTES:
            logger.error("LI-COR response exceeded %d-byte cap for %s", _MAX_BODY_BYTES, endpoint)
            return None
        return resp.text

    logger.error("Gave up after rate-limit retries for %s", endpoint)
    return None


def _prepare(session: Session) -> list[_LicorWork]:
    """Build the work list: active fetch_url rows whose parser POSTs. Borrows session."""
    work: list[_LicorWork] = []
    for fu in get_active_fetch_urls(session):
        parser_cls = get_parser_class(fu.parser) if fu.parser else None
        if parser_cls is None or getattr(parser_cls, "transport", "GET") != "POST":
            continue
        sources = list(fu.sources)
        work.append(
            _LicorWork(
                url=fu.url,
                source_map={s.name: s.id for s in sources},
                source_id=sources[0].id if len(sources) == 1 else None,
                fetch_url_id=fu.id,
            )
        )
    return work


def _fetch_one(url: str) -> str | None:
    """Build, validate, and POST one LI-COR request. Returns text or None.

    A bad config URL or SSRF-blocked endpoint fails closed (logged, no network
    call) so one misconfigured row never fetches the wrong host or crashes the run.
    """
    try:
        endpoint, body = build_request(url)
    except ValueError as exc:
        logger.error("Bad LI-COR config url, skipping (no fetch): %s", exc)
        return None
    try:
        _validate_url(endpoint)  # SSRF defense-in-depth (host is already pinned)
    except ValueError as exc:
        logger.error("LI-COR endpoint failed SSRF validation: %s", exc)
        return None
    return _post(endpoint, body, FETCH_TIMEOUT)


def _store(session: Session, work: _LicorWork, text: str, dry_run: bool) -> None:
    """Parse + store one payload via LicorParser (commit-per-URL). Borrows session.

    A bad single payload never kills the batch — both except branches roll back
    its partial work.
    """
    try:
        parser = LicorParser(
            url=work.url,
            session=session,
            source_id=work.source_id,
            source_map=work.source_map,
            fetch_url_id=work.fetch_url_id,
            dry_run=dry_run,
        )
        count = parser.parse(text)
        if parser.unknown_stations and not dry_run:
            logger.warning(
                "LI-COR %s dropped %d obs from undeclared station(s): %s",
                work.url,
                parser.dropped_obs_count,
                ", ".join(sorted(parser.unknown_stations)),
            )
        if work.fetch_url_id and not dry_run:
            state = session.get(FetchState, work.fetch_url_id) or FetchState(
                fetch_url_id=work.fetch_url_id
            )
            state.last_fetched_at = datetime.now(UTC)
            session.add(state)
        logger.debug("  %d updates from %s", count, work.url)
        if not dry_run:
            session.commit()
    except (ValueError, KeyError, LookupError) as exc:
        session.rollback()
        logger.error("Parse/data error for %s: %s", work.url, exc)
    except Exception:
        session.rollback()
        logger.exception("Unexpected error for %s", work.url)


def fetch_licor(args: argparse.Namespace) -> int:
    """Fetch LI-COR timeseries data and store observations.

    Soft pipeline step (a LI-COR outage logs + leaves the gauge stale but never
    fails the run). Always returns 0; a transient endpoint failure is logged, not
    alerted, matching the GET fetch path's handling of fetch errors.
    """
    ensure_all_loaded()
    dry_run = getattr(args, "dry_run", False)
    show_name = getattr(args, "show_name", False)
    if dry_run:
        print("Dry run mode — no data will be stored")

    # --- Phase 1: prepare work items (short read-only session) ---
    session = get_session()
    try:
        work = _prepare(session)
    finally:
        session.close()

    print(f"Found {len(work)} LI-COR source(s) to fetch")
    if not work:
        return 0

    # --- Phase 2: POST each request (no DB session held) ---
    content: dict[str, str | None] = {}
    for w in work:
        if show_name:
            print(f"Fetching {w.url}")
        content[w.url] = _fetch_one(w.url)

    # --- Phase 3: parse + store (short write session, commit-per-URL) ---
    session = get_session()
    try:
        for w in work:
            text = content.get(w.url)
            if text is None:
                continue
            _store(session, w, text, dry_run)
        if dry_run:
            session.rollback()
        else:
            print("Committed to database")
    finally:
        session.close()
    return 0
