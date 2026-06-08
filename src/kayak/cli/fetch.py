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

from kayak.cli.init_db import canonical_agency, sync_sources
from kayak.config import FETCH_BUDGET
from kayak.config_data import load_sources
from kayak.db.engine import get_session
from kayak.db.models import FetchUrl, Source
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


def fetch(args: argparse.Namespace) -> None:
    """Fetch data from remote agencies, parse, and store in database.

    Phase-banner structure preserved: (1) prepare work items inside a
    short read-only session; (2) network I/O with no session held;
    (3) parse + store inside a short write session. Sessions are opened
    in this function and passed to helpers — helpers never acquire
    their own session.
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
        return

    yaml_sources = _filter_yaml_sources(load_sources(), args.parser_filter, args.url_filter)
    print(f"Found {len(yaml_sources)} URL sources to process")

    # --- Phase 1: Prepare work items (short read-only session) ---
    session = get_session()
    try:
        # Sync YAML → fetch_url table so new/changed URLs are available
        sync_sources(session)
        session.commit()
        work_items = _prepare_work_items(session, yaml_sources, args)
    finally:
        session.close()

    # --- Phase 2: Fetch content (no DB session held) ---
    content_map = _fetch_content(work_items, args)

    # --- Phase 3: Parse and store (short write session) ---
    session = get_session()
    try:
        _parse_and_store(session, work_items, content_map, args)
        if args.dry_run:
            session.rollback()
        else:
            print("Committed to database")
    finally:
        session.close()


def _filter_yaml_sources(
    yaml_sources: list[dict],
    parser_filter: str | None,
    url_filter: str | None,
) -> list[dict]:
    """Apply optional --parser-filter / --url-filter to the YAML source list."""
    if parser_filter:
        yaml_sources = [s for s in yaml_sources if s["parser"] == parser_filter]
    if url_filter:
        yaml_sources = [s for s in yaml_sources if url_filter in s["url"]]
    return yaml_sources


def _build_fetch_work(
    session: Session,
    src_def: dict,
    url: str,
    parser_name: str,
) -> _FetchWork:
    """Resolve a YAML source row into a fully-populated _FetchWork.

    Caller has already filtered by hour-allow + parser-known + fetch_only
    short-circuit. This helper does the fetch_url lookup + source-map
    flattening only.
    """
    fetch_url = session.execute(
        select(FetchUrl).where(FetchUrl.url == src_def["url"])
    ).scalar_one_or_none()

    source_id = None
    source_map: dict[str, int] = {}
    source_tz_map: dict[str, str] = {}
    fetch_url_id = None
    if fetch_url is not None:
        fetch_url_id = fetch_url.id
        sources = fetch_url.sources
        source_map = {s.name: s.id for s in sources}
        source_tz_map = {s.name: s.timezone for s in sources if s.timezone}
        if len(sources) == 1:
            source_id = sources[0].id

    return _FetchWork(
        url=url,
        raw_url=src_def["url"],
        parser_name=parser_name,
        source_id=source_id,
        source_map=source_map,
        source_tz_map=source_tz_map,
        fetch_url_id=fetch_url_id,
    )


def _prepare_work_items(
    session: Session,
    yaml_sources: list[dict],
    args: argparse.Namespace,
) -> list[_FetchWork]:
    """Build the list of fetch work items. Session is borrowed, not owned.

    Both `fetch_only` short-circuit and `parser_cls` pre-flight are
    intentional and stay here (so we don't carry a non-fetchable URL into
    Phase 2's async layer, and so a typo'd parser_name surfaces before any
    network I/O).
    """
    work_items: list[_FetchWork] = []
    for src_def in yaml_sources:
        hours = src_def.get("hours", "")
        if not args.ignore_constraints and not _hour_allowed(hours):
            logger.debug("Skipping %s (hour constraint)", src_def["url"])
            continue

        url = args.url_prefix + src_def["url"]
        parser_name = args.parser_type or src_def["parser"]

        if args.show_name:
            print(f"Processing {url} parser={parser_name}")
        else:
            logger.info("Processing %s parser=%s", url, parser_name)

        if args.fetch_only:
            work_items.append(
                _FetchWork(
                    url=url,
                    raw_url=src_def["url"],
                    parser_name=parser_name,
                    source_id=None,
                )
            )
            continue

        if get_parser_class(parser_name) is None:
            logger.error("Unknown parser '%s'", parser_name)
            continue

        work_items.append(_build_fetch_work(session, src_def, url, parser_name))
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
) -> None:
    """Phase 3: parse + store each fetched payload. Session is borrowed.

    The per-URL commit inside the loop is the SQLite-writer-lock-release
    pattern — it stays in this function (not hoisted). The two except
    branches stay distinct: `logger.error` for expected parser/data
    errors (already actionable); `logger.exception` (with traceback) for
    anything else so an unexpected crash is debuggable.
    """
    for w in work_items:
        text_content = content_map.get(w.url)
        if text_content is None:
            continue

        if args.fetch_only:
            continue

        try:
            parser_cls = get_parser_class(w.parser_name)
            if parser_cls is None:
                continue

            parser = parser_cls(
                url=w.url,
                session=session,
                source_id=w.source_id,
                source_map=w.source_map,
                source_tz_map=w.source_tz_map,
                dry_run=args.dry_run,
                fetch_url_id=w.fetch_url_id,
                agency=canonical_agency(w.parser_name),
            )
            count = parser.parse(text_content)

            if w.fetch_url_id and not args.dry_run and not args.input_dir:
                fetch_url = session.get(FetchUrl, w.fetch_url_id)
                if fetch_url:
                    fetch_url.last_fetched_at = datetime.now(UTC)

            logger.debug("  %d updates", count)

            # Commit after each URL to release the SQLite writer lock
            # between URLs; otherwise concurrent PHP readers can hit
            # SQLITE_BUSY while the pipeline is running.
            if not args.dry_run:
                session.commit()

        except (ValueError, KeyError, LookupError) as e:
            session.rollback()
            logger.error("Parse/data error for %s: %s", w.url, e)
            continue
        except Exception:
            # Don't let a single bad URL kill the rest of the batch —
            # log with traceback and move on. KeyboardInterrupt /
            # SystemExit (BaseException) still propagate.
            session.rollback()
            logger.exception("Unexpected error for %s", w.url)
            continue


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
            if not dry_run:
                session.commit()
        finally:
            session.close()
