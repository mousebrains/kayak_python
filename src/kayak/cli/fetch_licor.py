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

from kayak.cli.fetch import _hour_allowed
from kayak.config import FETCH_TIMEOUT, FETCH_USER_AGENT
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
    """One LI-COR fetch unit prepared (config validated) before network I/O."""

    url: str
    endpoint: str
    body: dict
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
    parser.add_argument(
        "-i", "--ignore-constraints", action="store_true", help="Ignore fetch_url hour constraints"
    )


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
    if not 1 <= interval <= 1440:
        raise ValueError(f"LI-COR url 'interval' must be 1-1440: {interval}")
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
    seen: dict[str, str] = {}
    for param, dtype in CHANNEL_PARAMS.items():
        uuid = _one(params, param)
        if not uuid:
            raise ValueError(f"LI-COR url missing '{param}' channel UUID")
        # Reject a UUID reused across params — it would map two data types to one
        # channel and silently mis-type one of them. Fail closed before any fetch.
        if uuid in seen:
            raise ValueError(
                f"LI-COR url reuses channel UUID {uuid!r} for '{seen[uuid]}' and '{param}'"
            )
        seen[uuid] = param
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
    # https only: the endpoint is a fixed HTTPS host. Allowing http would let a
    # typo'd row fail silently (redirects are disabled, so the http→https 3xx is
    # treated as a fetch failure that — being transient, not a config error —
    # leaves the gauge stale with no alert), and plaintext would expose the
    # request to in-path tampering.
    if parsed.scheme != "https":
        raise ValueError(f"LI-COR url scheme must be https: {url!r}")
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
            # allow_redirects=False: a 3xx to an internal host (169.254.169.254,
            # loopback, RFC1918) would otherwise be followed automatically,
            # bypassing the host-pin + _validate_url SSRF checks, which only
            # validate the initial endpoint. Mirrors http_client.py's GET path.
            resp = requests.post(
                endpoint,
                json=body,
                # Identify as the configured pipeline UA, not python-requests —
                # courteous + identifiable for an undocumented third-party endpoint
                # we want to keep access to (mirrors the shared GET client).
                headers={"Accept": "application/json", "User-Agent": FETCH_USER_AGENT},
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error("LI-COR request failed for %s: %s", endpoint, exc)
            return None

        if resp.status_code == 429:
            wait = 2**attempt
            logger.warning("LI-COR rate limited (429), waiting %ds", wait)
            time.sleep(wait)
            continue
        # >= 300, not >= 400: with redirects disabled a 3xx is a failure (and a
        # possible SSRF attempt), not something to follow.
        if resp.status_code >= 300:
            logger.error("HTTP %d from LI-COR %s", resp.status_code, endpoint)
            return None
        # Bounds what we hand the parser, not transfer: `requests` has already
        # buffered the body by here (no stream=True). Fine for a few-KB API on a
        # pinned, redirect-free host; the Content-Length check just rejects an
        # over-cap body a hair earlier.
        content_length = resp.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
            logger.error("LI-COR Content-Length %s exceeds cap for %s", content_length, endpoint)
            return None
        if len(resp.content) > _MAX_BODY_BYTES:
            logger.error("LI-COR response exceeded %d-byte cap for %s", _MAX_BODY_BYTES, endpoint)
            return None
        return resp.text

    logger.error("Gave up after rate-limit retries for %s", endpoint)
    return None


def _prepare(session: Session, ignore_constraints: bool) -> tuple[list[_LicorWork], int]:
    """Build + validate the work list. Borrows session.

    Returns ``(work_items, config_error_count)``. Each POST-parser ``fetch_url``
    is validated up front (``build_request`` + the single-source requirement);
    a row that fails is logged at ERROR, skipped, and counted — a config error is
    a *permanent* misconfiguration that must surface in the exit code (alert),
    unlike a transient POST failure later. Config validation runs before any
    network I/O.

    ``fetch_url.hours`` is honored (unless ``ignore_constraints``), matching the
    GET fetch path — so a row throttled to e.g. ``hours: "6,12,18"`` POSTs only in
    those UTC hours instead of every pipeline run. An hour-skipped row is not
    fetched and not config-checked (it'll alert in its own window if misconfigured).
    """
    work: list[_LicorWork] = []
    config_errors = 0
    for fu in get_active_fetch_urls(session):
        parser_cls = get_parser_class(fu.parser) if fu.parser else None
        if parser_cls is None or getattr(parser_cls, "transport", "GET") != "POST":
            continue
        if not ignore_constraints and not _hour_allowed(fu.hours or ""):
            logger.debug("Skipping %s (hour constraint)", fu.url)
            continue
        sources = list(fu.sources)
        if len(sources) != 1:
            # A LI-COR dashboard is one physical station → exactly one source.
            # >1 would leave source_id None and silently drop every obs; 0 is an
            # orphan. Refuse loudly rather than mis-handle.
            logger.error(
                "LI-COR fetch_url %s must have exactly one source (has %d) — skipping",
                fu.url,
                len(sources),
            )
            config_errors += 1
            continue
        try:
            endpoint, body = build_request(fu.url)
        except ValueError as exc:
            logger.error("Bad LI-COR config url, skipping (no fetch): %s", exc)
            config_errors += 1
            continue
        work.append(
            _LicorWork(
                url=fu.url,
                endpoint=endpoint,
                body=body,
                source_map={s.name: s.id for s in sources},
                source_id=sources[0].id,
                fetch_url_id=fu.id,
            )
        )
    return work, config_errors


def _fetch_one(work: _LicorWork) -> str | None:
    """Validate (SSRF) and POST one prepared request. Returns text or None.

    ``build_request`` already ran in ``_prepare`` (fail-closed config check); this
    re-runs the ``_validate_url`` SSRF guard the GET client uses before the POST.

    A hung endpoint is bounded by the per-request ``FETCH_TIMEOUT`` only — there's
    no batch wall-clock budget (the ``--budget``/``async_fetch_many`` machinery is
    GET-batch-specific). This matches the sibling ``fetch-usgs-ogc`` step, and with
    one LI-COR row the per-request timeout is already the effective cap.
    """
    try:
        _validate_url(work.endpoint)  # SSRF defense-in-depth (host is already pinned)
    except ValueError as exc:
        logger.error("LI-COR endpoint failed SSRF validation: %s", exc)
        return None
    return _post(work.endpoint, work.body, FETCH_TIMEOUT)


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

    Soft pipeline step: a LI-COR outage (transient POST failure) logs + leaves
    the gauge stale but never fails the run (returns 0) — and, with no pipeline
    step requiring it, never cascade-skips build. Returns 1 only on a *config*
    error (a malformed dataset ``fetch_url`` or a non-single-source row), which is
    permanent and must alert via the soft-step non-zero exit.
    """
    ensure_all_loaded()
    dry_run = getattr(args, "dry_run", False)
    show_name = getattr(args, "show_name", False)
    ignore_constraints = getattr(args, "ignore_constraints", False)
    if dry_run:
        print("Dry run mode — no data will be stored")

    # --- Phase 1: prepare + validate work items (short read-only session) ---
    session = get_session()
    try:
        work, config_errors = _prepare(session, ignore_constraints)
    finally:
        session.close()

    exit_code = 1 if config_errors else 0
    print(f"Found {len(work)} LI-COR source(s) to fetch")
    if not work:
        return exit_code

    # --- Phase 2: POST each request (no DB session held) ---
    content: dict[str, str | None] = {}
    for w in work:
        if show_name:
            print(f"Fetching {w.url}")
        content[w.url] = _fetch_one(w)

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
    return exit_code
