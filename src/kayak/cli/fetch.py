"""Data fetcher command (replaces fetcher.C).

Fetches data from remote government agencies, parses it, and stores
observations in the database.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import click

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


@click.command("fetch")
@click.option("-d", "--dry-run", is_flag=True, help="Do not store data")
@click.option("-f", "--fetch-only", is_flag=True, help="Fetch but do not parse")
@click.option("-I", "--input-dir", type=click.Path(exists=True),
              help="Read previously saved files instead of fetching from network")
@click.option("-i", "--ignore-constraints", is_flag=True, help="Ignore hour constraints")
@click.option("-n", "--show-name", is_flag=True, help="Show URL being fetched")
@click.option("-o", "--output-dir", type=click.Path(), help="Save fetched data to directory")
@click.option("-P", "--url-prefix", default="", help="Prepend to all URLs")
@click.option("-p", "--parser-filter", default=None, help="Filter by parser type")
@click.option("-t", "--parser-type", default=None, help="Force parser type")
@click.option("-u", "--url-filter", default=None, help="Filter by URL substring")
@click.option("-U", "--single-url", default=None, help="Fetch a single URL")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
def fetch_cmd(
    dry_run, fetch_only, input_dir, ignore_constraints, show_name,
    output_dir, url_prefix, parser_filter, parser_type,
    url_filter, single_url, verbose,
):
    """Fetch data from remote agencies, parse, and store in database."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    ensure_all_loaded()

    if dry_run:
        click.echo("Dry run mode — no data will be stored")

    if input_dir:
        click.echo(f"Reading from saved files in {input_dir}")

    if single_url and parser_type:
        _fetch_single(single_url, parser_type, url_prefix, output_dir,
                       input_dir, verbose, dry_run, fetch_only)
        return

    # Load sources from YAML config
    yaml_sources = load_sources()

    # Apply filters
    if parser_filter:
        yaml_sources = [s for s in yaml_sources if s["parser"] == parser_filter]
    if url_filter:
        yaml_sources = [s for s in yaml_sources if url_filter in s["url"]]

    click.echo(f"Found {len(yaml_sources)} URL sources to process")

    session = get_session()
    try:
        for src_def in yaml_sources:
            hours = src_def.get("hours", "")
            if not ignore_constraints and not _hour_allowed(hours):
                if verbose:
                    click.echo(f"Skipping {src_def['url']} (hour constraint)")
                continue

            url = url_prefix + src_def["url"]
            parser_name = parser_type or src_def["parser"]

            if show_name or verbose:
                click.echo(f"Processing {url} parser={parser_name}")

            try:
                text_content = _get_content(
                    url, src_def["url"], input_dir, output_dir, verbose
                )
                if text_content is None:
                    continue

                if not fetch_only:
                    parser_cls = get_parser_class(parser_name)
                    if parser_cls is None:
                        click.echo(
                            f"  Unknown parser '{parser_name}'", err=True
                        )
                        continue

                    # Look up the FetchUrl to update last_fetched_at
                    fetch_url = session.query(FetchUrl).filter_by(
                        url=src_def["url"]
                    ).first()

                    parser = parser_cls(
                        url=url, session=session,
                        source_id=fetch_url.id if fetch_url else None,
                        verbose=verbose, dry_run=dry_run,
                    )
                    count = parser.parse(text_content)

                    if fetch_url and not dry_run and not input_dir:
                        fetch_url.last_fetched_at = datetime.utcnow()

                    if verbose:
                        click.echo(f"  {count} updates")

            except Exception as e:
                click.echo(f"  Exception for {url}: {e}", err=True)
                continue

        if not dry_run:
            session.commit()
            click.echo("Committed to database")
        else:
            session.rollback()

    finally:
        session.close()


def _get_content(url, raw_url, input_dir, output_dir, verbose):
    """Get text content either from a saved file or by fetching the URL.

    Returns the text content, or None if the content could not be obtained.
    """
    if input_dir:
        file_path = Path(input_dir) / raw_url.lstrip("/")
        if not file_path.exists():
            if verbose:
                click.echo(f"  No saved file: {file_path}", err=True)
            return None
        if verbose:
            click.echo(f"  Reading {file_path}")
        return file_path.read_text(encoding="utf-8", errors="replace")

    from kayak.utils.http_client import fetch

    result = fetch(url)
    if not result.ok:
        click.echo(f"  Error: {result.error}", err=True)
        return None

    if result.status_code >= 400:
        click.echo(f"  HTTP {result.status_code} for {url}", err=True)
        return None

    if output_dir:
        out_path = Path(output_dir) / raw_url.lstrip("/")
        result.write_file(str(out_path))

    return result.text


def _fetch_single(
    url, parser_name, url_prefix, output_dir, input_dir,
    verbose, dry_run, fetch_only,
):
    """Fetch and parse a single URL (the -U -t mode)."""
    full_url = url_prefix + url

    text_content = _get_content(full_url, url, input_dir, output_dir, verbose)
    if text_content is None:
        return

    if not fetch_only:
        parser_cls = get_parser_class(parser_name)
        if parser_cls is None:
            click.echo(f"Unknown parser '{parser_name}'", err=True)
            return

        session = get_session()
        try:
            parser = parser_cls(
                url=full_url, session=session,
                verbose=verbose, dry_run=dry_run,
            )
            count = parser.parse(text_content)
            click.echo(f"{count} database updates")
            if not dry_run:
                session.commit()
        finally:
            session.close()
