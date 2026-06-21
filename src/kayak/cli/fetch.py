"""Data fetcher command.

Fetches data from remote government agencies, parses it, and stores
observations in the database.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.config import FETCH_BUDGET
from kayak.db.engine import get_session
from kayak.db.models import FetchState, FetchUrl, Source
from kayak.db.sources import get_active_fetch_urls
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import ensure_all_loaded, get_parser_class

logger = logging.getLogger(__name__)


def _safe_subpath(base_dir: Path, raw_url: str) -> Path:
    """Resolve raw_url under base_dir, rejecting path traversal."""
    candidate = (base_dir / raw_url.lstrip("/")).resolve()
    if not candidate.is_relative_to(base_dir.resolve()):
        raise ValueError(f"Path traversal detected: {raw_url!r} escapes {base_dir}")
    return candidate


def _hour_allowed(hours_spec: str, now: datetime | None = None) -> bool:
    """Check if the current hour is allowed by the hours constraint.

    ``now`` defaults to ``datetime.now(UTC)`` but tests can inject a fixed
    clock to avoid flakiness around the hour boundary.

    Empty / whitespace-only string means all hours are allowed (the
    "unconstrained" path that the YAML's default value falls through to).
    A garbled spec (non-integer tokens, e.g. ``"abc,xyz"``) fails closed —
    returning False so a data-entry typo doesn't silently disable the
    constraint and fetch every hour.
    """
    if not hours_spec or not hours_spec.strip():
        return True
    current_hour = (now or datetime.now(UTC)).hour
    try:
        allowed = {int(h.strip()) for h in hours_spec.split(",") if h.strip()}
    except ValueError:
        logger.warning("Invalid hours spec %r — treating as disallowed", hours_spec)
        return False
    return current_hour in allowed


def addArgs_options(parser: argparse.ArgumentParser) -> None:
    """Add fetch-specific options to a parser."""
    parser.add_argument("-d", "--dry-run", action="store_true", help="Do not store data")
    parser.add_argument("-f", "--fetch-only", action="store_true", help="Fetch but do not parse")
    parser.add_argument(
        "-I",
        "--input-dir",
        default=None,
        help="Read previously saved files instead of fetching from network",
    )
    parser.add_argument(
        "-i", "--ignore-constraints", action="store_true", help="Ignore hour constraints"
    )
    parser.add_argument("-n", "--show-name", action="store_true", help="Show URL being fetched")
    parser.add_argument("-o", "--output-dir", default=None, help="Save fetched data to directory")
    parser.add_argument("-P", "--url-prefix", default="", help="Prepend to all URLs")
    parser.add_argument("-p", "--parser-filter", default=None, help="Filter by parser type")
    parser.add_argument("-t", "--parser-type", default=None, help="Force parser type")
    parser.add_argument("-u", "--url-filter", default=None, help="Filter by URL substring")
    parser.add_argument("-U", "--single-url", default=None, help="Fetch a single URL")
    parser.add_argument(
        "--concurrency", type=int, default=8, help="Max concurrent requests per host (default: 8)"
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=FETCH_BUDGET,
        help=(
            "Wall-clock budget for the whole fetch batch in seconds "
            f"(default: {FETCH_BUDGET}; 0 disables)"
        ),
    )


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'fetch' subcommand."""
    parser = subparsers.add_parser(
        "fetch", help="Fetch data from remote agencies, parse, and store in database"
    )
    parser.set_defaults(func=fetch)
    addArgs_options(parser)


@dataclass
class _FetchWork:
    """A single unit of fetch work prepared before I/O begins."""

    url: str
    raw_url: str
    parser_name: str
    source_id: int | None
    source_map: dict[str, int] = field(default_factory=dict)
    source_tz_map: dict[str, str] = field(default_factory=dict)
    fetch_url_id: int | None = None
    # Per-URL policy for a parser-emitted station with no `source` row. NULL/''/
    # 'reject' -> reject (alert; fetch exits non-zero); 'ignore' -> drop quietly.
    unknown_station_policy: str | None = None


def fetch(args: argparse.Namespace) -> int:
    """Fetch data from remote agencies, parse, and store in database.

    Phase-banner structure preserved: (1) prepare work items inside a
    short read-only session; (2) network I/O with no session held;
    (3) parse + store inside a short write session. Sessions are opened
    in this function and passed to helpers — helpers never acquire
    their own session.

    The work-list comes from the DB's active ``fetch_url`` rows (synced from
    the dataset CSVs by ``levels sync-metadata``), not the engine-packaged
    ``sources.yaml`` (dataset-separation S1). Returns the process exit code:
    ``1`` if any URL emitted an undeclared station under the default ``reject``
    policy (its known sibling stations are still saved, but the non-zero exit
    drives the systemd ``OnFailure`` alert), else ``0``.
    """

    ensure_all_loaded()

    if args.dry_run:
        print("Dry run mode — no data will be stored")
    if args.input_dir:
        print(f"Reading from saved files in {args.input_dir}")

    if args.single_url and args.parser_type:
        _fetch_single(
            args.single_url,
            args.parser_type,
            args.url_prefix,
            args.output_dir,
            args.input_dir,
            args.dry_run,
            args.fetch_only,
        )
        return 0

    # --- Phase 1: Prepare work items from the DB (short read-only session) ---
    session = get_session()
    try:
        work_items = _prepare_work_items(session, args)
    finally:
        session.close()

    # --- Phase 2: Fetch content (no DB session held) ---
    content_map = _fetch_content(work_items, args)

    # --- Phase 3: Parse and store (short write session) ---
    rejected: list[str] = []
    session = get_session()
    try:
        rejected = _parse_and_store(session, work_items, content_map, args)
        if args.dry_run:
            session.rollback()
        else:
            print("Committed to database")
    finally:
        session.close()

    if rejected:
        logger.error(
            "fetch: %d URL(s) emitted undeclared stations under reject policy "
            "(known stations were still saved): %s",
            len(rejected),
            ", ".join(rejected),
        )
        return 1
    return 0


def _filter_fetch_urls(
    fetch_urls: list[FetchUrl],
    parser_filter: str | None,
    url_filter: str | None,
) -> list[FetchUrl]:
    """Apply optional --parser-filter / --url-filter to the active fetch_url rows."""
    if parser_filter:
        fetch_urls = [fu for fu in fetch_urls if fu.parser == parser_filter]
    if url_filter:
        fetch_urls = [fu for fu in fetch_urls if url_filter in fu.url]
    return fetch_urls


def _build_fetch_work(fetch_url: FetchUrl, url: str, parser_name: str) -> _FetchWork:
    """Flatten a ``fetch_url`` row (with its eager-loaded sources) into a
    fully-populated _FetchWork.

    Caller has already filtered by hour-allow + parser-known + fetch_only
    short-circuit. ``source_id`` is set only when the URL has exactly one source
    (single-station feeds); multi-station feeds dispatch via ``source_map``.
    """
    sources = fetch_url.sources
    source_map = {s.name: s.id for s in sources}
    source_tz_map = {s.name: s.timezone for s in sources if s.timezone}
    source_id = sources[0].id if len(sources) == 1 else None

    return _FetchWork(
        url=url,
        raw_url=fetch_url.url,
        parser_name=parser_name,
        source_id=source_id,
        source_map=source_map,
        source_tz_map=source_tz_map,
        fetch_url_id=fetch_url.id,
        unknown_station_policy=fetch_url.unknown_station_policy,
    )


def _prepare_work_items(
    session: Session,
    args: argparse.Namespace,
) -> list[_FetchWork]:
    """Build the list of fetch work items from the DB. Session is borrowed.

    Reads the active ``fetch_url`` rows (synced from the dataset CSVs), applies
    the optional name/url filters, then per row: the hour-allow check, the
    ``fetch_only`` short-circuit, and the ``parser_cls`` pre-flight (so we don't
    carry a non-fetchable URL into Phase 2's async layer, and a typo'd / unknown
    parser surfaces before any network I/O).
    """
    fetch_urls = _filter_fetch_urls(
        get_active_fetch_urls(session), args.parser_filter, args.url_filter
    )
    print(f"Found {len(fetch_urls)} URL sources to process")

    work_items: list[_FetchWork] = []
    for fu in fetch_urls:
        if not args.ignore_constraints and not _hour_allowed(fu.hours or ""):
            logger.debug("Skipping %s (hour constraint)", fu.url)
            continue

        url = args.url_prefix + fu.url
        parser_name = args.parser_type or fu.parser

        # Non-GET parsers (e.g. licor, transport="POST") ride a dedicated step,
        # not the shared async GET client — skip them here so `levels fetch`
        # doesn't GET a POST-only endpoint every run. An unknown parser_name
        # (parser_cls is None) is left to the existing error path below.
        parser_cls = get_parser_class(parser_name) if parser_name else None
        transport = getattr(parser_cls, "transport", "GET")
        if parser_cls is not None and transport != "GET":
            logger.debug(
                "Skipping %s (parser %r transport=%s, handled by its own step)",
                fu.url,
                parser_name,
                transport,
            )
            continue

        if args.show_name:
            print(f"Processing {url} parser={parser_name}")
        else:
            logger.info("Processing %s parser=%s", url, parser_name)

        if args.fetch_only:
            work_items.append(
                _FetchWork(
                    url=url,
                    raw_url=fu.url,
                    parser_name=parser_name or "",
                    source_id=None,
                )
            )
            continue

        if parser_name is None or get_parser_class(parser_name) is None:
            logger.error("Unknown parser %r for %s", parser_name, fu.url)
            continue

        work_items.append(_build_fetch_work(fu, url, parser_name))
    return work_items


def _fetch_content(
    work_items: list[_FetchWork],
    args: argparse.Namespace,
) -> dict[str, str | None]:
    """Phase 2: pull content for every work item. NO DB session is held."""
    if args.input_dir:
        return {w.url: _get_content_from_file(w.raw_url, args.input_dir) for w in work_items}
    if not work_items:
        return {}

    from kayak.utils.http_client import async_fetch_many

    urls = [w.url for w in work_items]
    results = asyncio.run(
        async_fetch_many(
            urls,
            concurrency_per_host=args.concurrency,
            budget=getattr(args, "budget", FETCH_BUDGET) or None,
        )
    )
    content_map: dict[str, str | None] = {}
    for w in work_items:
        result = results[w.url]
        if not result.ok:
            logger.error("Fetch error for %s: %s", w.url, result.error)
            content_map[w.url] = None
        elif result.status_code >= 400:
            logger.error("HTTP %d for %s", result.status_code, w.url)
            content_map[w.url] = None
        else:
            if args.output_dir:
                out_path = _safe_subpath(Path(args.output_dir), w.raw_url)
                result.write_file(str(out_path))
            content_map[w.url] = result.text
    return content_map


def _parse_and_store(
    session: Session,
    work_items: list[_FetchWork],
    content_map: dict[str, str | None],
    args: argparse.Namespace,
) -> list[str]:
    """Phase 3: parse + store each fetched payload. Session is borrowed.

    The per-URL commit inside the loop is the SQLite-writer-lock-release
    pattern — it stays in this function (not hoisted). The two except
    branches stay distinct: `logger.error` for expected parser/data
    errors (already actionable); `logger.exception` (with traceback) for
    anything else so an unexpected crash is debuggable.

    Returns the URLs that emitted an undeclared station under the default
    ``reject`` policy (their known sibling stations are still committed); the
    caller turns a non-empty list into a non-zero exit so monitoring alerts.
    """
    rejected: list[str] = []
    for w in work_items:
        if _process_work_item(session, w, content_map, args):
            rejected.append(w.url)
    return rejected


def _process_work_item(
    session: Session,
    w: _FetchWork,
    content_map: dict[str, str | None],
    args: argparse.Namespace,
) -> bool:
    """Parse + store one URL's payload (commit-per-URL releases the SQLite writer
    lock between URLs). Returns True if the URL must be rejected (undeclared
    station under the default reject policy) so the caller can flag a non-zero
    fetch exit. A bad single URL never kills the batch — both except branches
    roll back its partial work and return False.
    """
    text_content = content_map.get(w.url)
    if text_content is None or args.fetch_only:
        return False

    try:
        parser_cls = get_parser_class(w.parser_name)
        if parser_cls is None:
            return False

        parser = parser_cls(
            url=w.url,
            session=session,
            source_id=w.source_id,
            source_map=w.source_map,
            source_tz_map=w.source_tz_map,
            dry_run=args.dry_run,
            fetch_url_id=w.fetch_url_id,
        )
        count = parser.parse(text_content)

        # Stations the feed emitted with no `source` row: their observations were
        # dropped (never auto-created — S1) while known sibling stations were
        # saved. The per-URL policy decides reject (alert) vs ignore.
        reject = bool(parser.unknown_stations) and not args.dry_run
        if reject:
            reject = _apply_unknown_station_policy(w, parser)

        if w.fetch_url_id and not args.dry_run and not args.input_dir:
            # Record the fetch timestamp in the runtime fetch_state table — never
            # mutate the dataset-owned fetch_url row here (SA / AC #6). Upsert by
            # fetch_url id (1:1); session.add on an already-persistent row is a no-op.
            state = session.get(FetchState, w.fetch_url_id) or FetchState(
                fetch_url_id=w.fetch_url_id
            )
            state.last_fetched_at = datetime.now(UTC)
            session.add(state)

        logger.debug("  %d updates", count)
        if not args.dry_run:
            session.commit()
        return reject

    except (ValueError, KeyError, LookupError) as e:
        session.rollback()
        logger.error("Parse/data error for %s: %s", w.url, e)
        return False
    except Exception:
        # Don't let a single bad URL kill the rest of the batch — log with
        # traceback and move on. KeyboardInterrupt / SystemExit (BaseException)
        # still propagate.
        session.rollback()
        logger.exception("Unexpected error for %s", w.url)
        return False


def _apply_unknown_station_policy(work: _FetchWork, parser: BaseParser) -> bool:
    """Log + classify a URL's undeclared stations per its ``unknown_station_policy``.

    Known sibling stations are already saved either way; this only decides whether
    the dropped unknowns are an alert (``reject`` → returns True → non-zero fetch
    exit) or expected churn (``ignore`` → returns False, warn with counts). Any
    value other than ``ignore`` (matched case-insensitively, whitespace-trimmed)
    is treated as reject (fail-safe). Note only multi-source URLs reach here — a
    single-source URL attributes any emitted station to its lone source, so an
    unrecognized station name there is not flagged (see BaseParser.dump_to_db).
    """
    stations = ", ".join(sorted(parser.unknown_stations))
    dropped = parser.dropped_obs_count
    if (work.unknown_station_policy or "").strip().lower() == "ignore":
        logger.warning(
            "Ignored %d observation(s) from %d undeclared station(s) on %s "
            "(unknown_station_policy=ignore): %s",
            dropped,
            len(parser.unknown_stations),
            work.url,
            stations,
        )
        return False
    logger.error(
        "Dropped %d observation(s) from %d undeclared station(s) on %s — no "
        "`source` row; declare them in the dataset or set "
        "unknown_station_policy=ignore: %s",
        dropped,
        len(parser.unknown_stations),
        work.url,
        stations,
    )
    return True


def _get_content_from_file(raw_url: str, input_dir: str) -> str | None:
    """Read content from a saved file in input_dir.

    Returns the text content, or None if the file does not exist.
    """
    file_path = _safe_subpath(Path(input_dir), raw_url)
    if not file_path.exists():
        logger.debug("No saved file: %s", file_path)
        return None
    logger.debug("Reading %s", file_path)
    return file_path.read_text(encoding="utf-8", errors="replace")


def _get_content(
    url: str, raw_url: str, input_dir: str | None, output_dir: str | None
) -> str | None:
    """Get text content either from a saved file or by fetching the URL.

    Returns the text content, or None if the content could not be obtained.
    Used by _fetch_single() for single-URL mode.
    """
    if input_dir:
        return _get_content_from_file(raw_url, input_dir)

    from kayak.utils.http_client import fetch as http_fetch

    result = http_fetch(url)
    if not result.ok:
        logger.error("Fetch error: %s", result.error)
        return None

    if result.status_code >= 400:
        logger.error("HTTP %d for %s", result.status_code, url)
        return None

    if output_dir:
        out_path = _safe_subpath(Path(output_dir), raw_url)
        result.write_file(str(out_path))

    return result.text


def _fetch_single(
    url: str,
    parser_name: str,
    url_prefix: str,
    output_dir: str | None,
    input_dir: str | None,
    dry_run: bool,
    fetch_only: bool,
) -> None:
    """Fetch and parse a single URL (the -U -t mode).

    Builds ``source_map`` / ``source_tz_map`` from every Source row already
    linked to an active fetch_url with this parser, so station dispatch and
    per-station TZ localization work for historical backfill URLs that
    aren't in sources.yaml themselves.
    """
    full_url = url_prefix + url

    text_content = _get_content(full_url, url, input_dir, output_dir)
    if text_content is None:
        return

    if not fetch_only:
        parser_cls = get_parser_class(parser_name)
        if parser_cls is None:
            logger.error("Unknown parser '%s'", parser_name)
            return

        session = get_session()
        try:
            # Pull every source that belongs to the requested parser, so
            # multi-station dispatch and TZ localization still work when
            # fetching an off-config URL (e.g. USBR start/end backfill).
            sources = session.scalars(
                select(Source).join(FetchUrl).where(FetchUrl.parser == parser_name)
            ).all()
            source_map = {s.name: s.id for s in sources}
            source_tz_map = {s.name: s.timezone for s in sources if s.timezone}

            parser = parser_cls(
                url=full_url,
                session=session,
                source_map=source_map,
                source_tz_map=source_tz_map,
                dry_run=dry_run,
            )
            count = parser.parse(text_content)
            print(f"{count} database updates")
            if parser.unknown_stations:
                # Single-URL backfill drops undeclared stations like the batch
                # path, but has no per-URL policy — surface them so a manual
                # backfill doesn't silently discard data (S1).
                logger.warning(
                    "Dropped %d observation(s) from %d undeclared station(s) on %s "
                    "(no `source` row): %s",
                    parser.dropped_obs_count,
                    len(parser.unknown_stations),
                    full_url,
                    ", ".join(sorted(parser.unknown_stations)),
                )
            if not dry_run:
                session.commit()
        finally:
            session.close()
