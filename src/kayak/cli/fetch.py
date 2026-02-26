"""Data fetcher command (replaces fetcher.C).

Fetches data from remote government agencies, parses it, and stores
measurements in the database.
"""

from __future__ import annotations

import logging
from datetime import datetime

import click

from kayak.db.engine import get_session
from kayak.db.models import URLParse
from kayak.parsers.registry import ensure_all_loaded, get_parser_class, get_parser_names
from kayak.utils.http_client import fetch

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
    dry_run, fetch_only, ignore_constraints, show_name,
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

    if single_url and parser_type:
        _fetch_single(single_url, parser_type, url_prefix, output_dir,
                       verbose, dry_run, fetch_only)
        return

    session = get_session()
    try:
        # Query URL sources from database
        query = session.query(URLParse).filter(URLParse.inactive.is_(None))
        if parser_filter:
            query = query.filter(URLParse.parser == parser_filter)
        if url_filter:
            query = query.filter(URLParse.url.contains(url_filter))

        records = query.all()
        click.echo(f"Found {len(records)} URL sources to fetch")

        for record in records:
            if not ignore_constraints and not _hour_allowed(record.hours):
                if verbose:
                    click.echo(f"Skipping {record.url} (hour constraint)")
                continue

            url = url_prefix + record.url
            parser_name = parser_type or record.parser

            if show_name or verbose:
                click.echo(f"Fetching {url} parser={parser_name}")

            try:
                result = fetch(url)
                if not result.ok:
                    click.echo(f"  Error: {result.error}", err=True)
                    continue

                if result.status_code >= 400:
                    click.echo(
                        f"  HTTP {result.status_code} for {url}", err=True
                    )
                    continue

                if output_dir:
                    from pathlib import Path
                    out_path = str(Path(output_dir) / record.url.lstrip("/"))
                    result.write_file(out_path)

                if not fetch_only:
                    parser_cls = get_parser_class(parser_name)
                    if parser_cls is None:
                        click.echo(
                            f"  Unknown parser '{parser_name}'", err=True
                        )
                        continue

                    parser = parser_cls(
                        url=url, session=session,
                        verbose=verbose, dry_run=dry_run,
                    )
                    count = parser.parse(result.text)
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


def _fetch_single(
    url, parser_name, url_prefix, output_dir,
    verbose, dry_run, fetch_only,
):
    """Fetch and parse a single URL (the -U -t mode)."""
    full_url = url_prefix + url

    result = fetch(full_url)
    if not result.ok:
        click.echo(f"Error: {result.error}", err=True)
        return

    if result.status_code >= 400:
        click.echo(f"HTTP {result.status_code} for {full_url}", err=True)
        return

    if output_dir:
        from pathlib import Path
        out_path = str(Path(output_dir) / url.lstrip("/"))
        result.write_file(out_path)

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
            count = parser.parse(result.text)
            click.echo(f"{count} database updates")
            if not dry_run:
                session.commit()
        finally:
            session.close()
