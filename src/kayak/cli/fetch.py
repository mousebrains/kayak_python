"""Data fetcher command (replaces fetcher.C).

Fetches data from remote government agencies, parses it, and stores
observations in the database.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from kayak.config_data import load_sources
from kayak.db.engine import get_session
from kayak.db.models import FetchUrl
from kayak.parsers.registry import ensure_all_loaded, get_parser_class

logger = logging.getLogger(__name__)


def _hour_allowed(hours_spec: str) -> bool:
    """Check if current hour is allowed by the hours constraint.

    Empty string means all hours are allowed.
    """
    if not hours_spec or not hours_spec.strip():
        return True
    current_hour = datetime.now().hour
    try:
        allowed = {int(h.strip()) for h in hours_spec.split(",") if h.strip()}
        return current_hour in allowed
    except ValueError:
        return True


def addArgs_options(parser):
    """Add fetch-specific options to a parser."""
    parser.add_argument("-d", "--dry-run", action="store_true", help="Do not store data")
    parser.add_argument("-f", "--fetch-only", action="store_true", help="Fetch but do not parse")
    parser.add_argument("-I", "--input-dir", default=None,
                        help="Read previously saved files instead of fetching from network")
    parser.add_argument("-i", "--ignore-constraints", action="store_true",
                        help="Ignore hour constraints")
    parser.add_argument("-n", "--show-name", action="store_true",
                        help="Show URL being fetched")
    parser.add_argument("-o", "--output-dir", default=None,
                        help="Save fetched data to directory")
    parser.add_argument("-P", "--url-prefix", default="", help="Prepend to all URLs")
    parser.add_argument("-p", "--parser-filter", default=None, help="Filter by parser type")
    parser.add_argument("-t", "--parser-type", default=None, help="Force parser type")
    parser.add_argument("-u", "--url-filter", default=None, help="Filter by URL substring")
    parser.add_argument("-U", "--single-url", default=None, help="Fetch a single URL")


def addArgs(subparsers):
    """Register the 'fetch' subcommand."""
    parser = subparsers.add_parser("fetch",
                                   help="Fetch data from remote agencies, parse, and store in database")
    parser.set_defaults(func=fetch)
    addArgs_options(parser)


def fetch(args):
    """Fetch data from remote agencies, parse, and store in database."""

    ensure_all_loaded()

    if args.dry_run:
        print("Dry run mode — no data will be stored")

    if args.input_dir:
        print(f"Reading from saved files in {args.input_dir}")

    if args.single_url and args.parser_type:
        _fetch_single(args.single_url, args.parser_type, args.url_prefix,
                       args.output_dir, args.input_dir, args.dry_run, args.fetch_only)
        return

    # Load sources from YAML config
    yaml_sources = load_sources()

    # Apply filters
    if args.parser_filter:
        yaml_sources = [s for s in yaml_sources if s["parser"] == args.parser_filter]
    if args.url_filter:
        yaml_sources = [s for s in yaml_sources if args.url_filter in s["url"]]

    print(f"Found {len(yaml_sources)} URL sources to process")

    session = get_session()
    try:
        for src_def in yaml_sources:
            hours = src_def.get("hours", "")
            if not args.ignore_constraints and not _hour_allowed(hours):
                logger.debug("Skipping %s (hour constraint)", src_def['url'])
                continue

            url = args.url_prefix + src_def["url"]
            parser_name = args.parser_type or src_def["parser"]

            if args.show_name:
                print(f"Processing {url} parser={parser_name}")
            else:
                logger.info("Processing %s parser=%s", url, parser_name)

            try:
                text_content = _get_content(
                    url, src_def["url"], args.input_dir, args.output_dir,
                )
                if text_content is None:
                    continue

                if not args.fetch_only:
                    parser_cls = get_parser_class(parser_name)
                    if parser_cls is None:
                        logger.error("Unknown parser '%s'", parser_name)
                        continue

                    # Look up the FetchUrl to update last_fetched_at
                    fetch_url = session.query(FetchUrl).filter_by(
                        url=src_def["url"]
                    ).first()

                    parser = parser_cls(
                        url=url, session=session,
                        source_id=fetch_url.id if fetch_url else None,
                        dry_run=args.dry_run,
                    )
                    count = parser.parse(text_content)

                    if fetch_url and not args.dry_run and not args.input_dir:
                        fetch_url.last_fetched_at = datetime.now(UTC)

                    logger.debug("  %d updates", count)

            except Exception as e:
                logger.error("Exception for %s: %s", url, e)
                continue

        if not args.dry_run:
            session.commit()
            print("Committed to database")
        else:
            session.rollback()

    finally:
        session.close()


def _get_content(url, raw_url, input_dir, output_dir):
    """Get text content either from a saved file or by fetching the URL.

    Returns the text content, or None if the content could not be obtained.
    """
    if input_dir:
        file_path = Path(input_dir) / raw_url.lstrip("/")
        if not file_path.exists():
            logger.debug("No saved file: %s", file_path)
            return None
        logger.debug("Reading %s", file_path)
        return file_path.read_text(encoding="utf-8", errors="replace")

    from kayak.utils.http_client import fetch as http_fetch

    result = http_fetch(url)
    if not result.ok:
        logger.error("Fetch error: %s", result.error)
        return None

    if result.status_code >= 400:
        logger.error("HTTP %d for %s", result.status_code, url)
        return None

    if output_dir:
        out_path = Path(output_dir) / raw_url.lstrip("/")
        result.write_file(str(out_path))

    return result.text


def _fetch_single(
    url, parser_name, url_prefix, output_dir, input_dir,
    dry_run, fetch_only,
):
    """Fetch and parse a single URL (the -U -t mode)."""
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
            parser = parser_cls(
                url=full_url, session=session,
                dry_run=dry_run,
            )
            count = parser.parse(text_content)
            print(f"{count} database updates")
            if not dry_run:
                session.commit()
        finally:
            session.close()
