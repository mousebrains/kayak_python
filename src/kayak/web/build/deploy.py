"""Build-pipeline orchestrator and per-file rename-deploy.

Owns the ``levels build`` CLI entry point, the staging→live rename-
replace deploy, and the per-state build/write pipeline that ties every
other module in this package together.
"""

import argparse
import filecmp
import hashlib
import json
import logging
import os
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.config import BASE_DIR, SITE_URL
from kayak.db.cache import get_all_latest_gauges
from kayak.db.engine import get_session
from kayak.db.gauges import get_calculated_gauge_ids
from kayak.db.models import DataType, Gauge, HucName, LatestGaugeObservation, Reach
from kayak.db.reaches import all_state_names, reaches_query
from kayak.web.build._shared import (
    _CSS_PATH,
    _FILTERS_JS_PATH,
    _JS_PATH,
    _NAV_STATES,
    _atomic_write,
    _css_link_tag,
    _load_css,
)
from kayak.web.build.exports import _build_csv, _build_text
from kayak.web.build.gauges import _write_gauges_page
from kayak.web.build.geojson import _build_reaches_state, _build_reaches_static
from kayak.web.build.levels import (
    _build_filter_bar,
    _build_html_table,
    _collect_filter_data,
    _get_builder_columns,
)
from kayak.web.build.shell import (
    _build_map_page,
    _build_page,
    _build_placeholder_page,
)
from kayak.web.build.sparklines import (
    _build_sparkline,
    _select_sparkline_series,
)

logger = logging.getLogger(__name__)


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'build' subcommand."""
    parser = subparsers.add_parser(
        "build", help="Generate static HTML/CSV/text files to output directory"
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", str(BASE_DIR / "public_html")),
        help="Output directory (default: $OUTPUT_DIR or public_html/)",
    )
    parser.set_defaults(func=build)


def _deploy_static_assets(output_dir: Path) -> None:
    """Copy the in-repo ``static/`` tree into ``output_dir/static/``.

    ``sw.js`` lands at the output root (not under ``static/``) so the
    service worker controls scope ``/``. Directories under ``static/``
    propagate via ``copytree`` with ``dirs_exist_ok=True``.
    """
    static_dir = output_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    for path in (BASE_DIR / "static").iterdir():
        if path.is_file():
            dst = output_dir if path.name == "sw.js" else static_dir
            shutil.copy2(path, dst / path.name)
        elif path.is_dir():
            shutil.copytree(path, static_dir / path.name, dirs_exist_ok=True)


def _deploy_php_files(output_dir: Path) -> None:
    """Install the PHP layer: top-level pages, ``includes/``, and ``style.css``.

    ``style.css`` lives at the output root because ``php/header.php`` reads
    it via ``__DIR__/../style.css`` — the hashed copy under ``static/`` is
    the cacheable variant served to static-HTML clients.
    """
    php_dir = BASE_DIR / "php"
    for path in php_dir.iterdir():
        if path.is_file() and path.suffix == ".php":
            shutil.copy2(path, output_dir / path.name)

    includes_dir = output_dir / "includes"
    includes_dir.mkdir(parents=True, exist_ok=True)
    for path in (php_dir / "includes").iterdir():
        if path.is_file():
            shutil.copy2(path, includes_dir / path.name)

    shutil.copy2(_CSS_PATH, output_dir / "style.css")


def _deploy_config_files(output_dir: Path) -> None:
    """Copy ``.htaccess``, ``404.html``, ``robots.txt`` from ``public_html/``.

    Only present files are copied — the rest of ``public_html/`` is the
    deploy target and gets populated by the generated content path.
    """
    repo_public = BASE_DIR / "public_html"
    for name in (".htaccess", "404.html", "robots.txt"):
        src = repo_public / name
        if src.is_file():
            shutil.copy2(src, output_dir / name)


def _deploy_source_files(output_dir: Path) -> None:
    """Copy source files from the repo into the output directory.

    Makes the output directory self-contained — no symlinks pointing
    back into the repo.  Covers static assets, PHP files, and config.
    """
    _deploy_static_assets(output_dir)
    _deploy_php_files(output_dir)
    _deploy_config_files(output_dir)


def _build_to_dir(output_dir: Path, args: argparse.Namespace) -> None:
    """Generate all site content into output_dir."""
    session = get_session()
    try:
        columns = _get_builder_columns()
        states = all_state_names(session)
        css = _load_css()
        css_hash = hashlib.sha256(css.encode()).hexdigest()[:10]
        css_link = _css_link_tag(css_hash)

        # All visible reaches — used for GeoJSON/map (includes map_only)
        all_reaches = reaches_query(session, visible_only=True, with_gauge=True)
        # Index/CSV/text reaches exclude map_only
        index_reaches = [r for r in all_reaches if not r.map_only]

        print(f"Building site: {len(index_reaches)} reaches")

        # Pre-load data for all reaches at gauge level
        gauge_ids = [r.gauge_id for r in all_reaches if r.gauge_id]
        calculated_gauge_ids = get_calculated_gauge_ids(session, gauge_ids)
        all_latest = get_all_latest_gauges(session, gauge_ids)

        # Deploy source files (static assets, PHP, config)
        _deploy_source_files(output_dir)

        # Generated static assets
        static_dir = output_dir / "static"
        shutil.copy2(_JS_PATH, static_dir / "levels.js")
        shutil.copy2(_FILTERS_JS_PATH, static_dir / "filters.js")
        # Content-hashed stylesheet — cacheable forever (URL changes on content
        # change). Sidecar lets PHP header.php pick up the same hashed URL so
        # static and dynamic pages share one cache entry.
        (static_dir / f"style-{css_hash}.css").write_text(css)
        (static_dir / "style.css.hash").write_text(css_hash)

        # Split the reach dataset into a stable-geometry file (long-cached,
        # content-hashed URL) and a hourly-changing per-reach status file.
        static_json = _build_reaches_static(all_reaches)
        state_json = _build_reaches_state(all_reaches, calculated_gauge_ids, all_latest)
        geom_hash = hashlib.sha256(static_json.encode()).hexdigest()[:10]
        _atomic_write(static_dir / "reaches-geom.json", static_json)
        _atomic_write(static_dir / "reaches-state.json", state_json)
        logger.info(
            "reaches-geom.json: %d bytes; reaches-state.json: %d bytes",
            len(static_json),
            len(state_json),
        )
        # Drop the retired combined file if an older build left one behind.
        with suppress(FileNotFoundError):
            (static_dir / "reaches.geojson").unlink()

        geom_url = f"/static/reaches-geom.json?v={geom_hash}"
        state_url = "/static/reaches-state.json"
        map_html = _build_map_page(css_link, states, geom_url, state_url)
        _atomic_write(output_dir / "map.html", map_html)

        # index.html = all reaches levels table (excludes map_only). Data
        # spans every state, so this is the "all page" that gets the state
        # filter group in the filter bar. state="" keeps the nav bar with
        # no state highlighted, the title as plain "River Levels", and the
        # companion CSV/text at levels.csv / levels.text rather than
        # mis-labeling them as Oregon-specific.
        _build_and_write(
            session,
            index_reaches,
            columns,
            "",
            states,
            css_link,
            output_dir,
            filename="index.html",
            preloaded=(calculated_gauge_ids, all_latest),
            is_all_page=True,
        )

        # gauges.html — supplemental all-gauges listing. Re-fetch the cache
        # over every gauge id it knows about so we also surface gauges with
        # no reach linkage (orphans / future reach work).
        gauges_latest = get_all_latest_gauges(
            session,
            list(session.scalars(select(LatestGaugeObservation.gauge_id).distinct())),
        )
        _write_gauges_page(session, gauges_latest, states, css_link, output_dir)

        # Links pages for all nav states (including Oregon)
        for state in _NAV_STATES:
            if state in states:
                links_page = _build_placeholder_page(css_link, states, state)
                _atomic_write(output_dir / f"{state}.html", links_page)

        _emit_sitemap(output_dir, states, index_reaches, session)
    finally:
        session.close()


def _emit_sitemap(
    output_dir: Path,
    states: list[str],
    reaches: list[Reach],
    session: Session,
) -> None:
    """Emit a sitemap.xml covering every public landing URL.

    Includes the index, each state's letter page, the gauges/map listings,
    the static prose pages, every visible reach's description page, and
    every gauge.php detail page. Dynamic search and account endpoints are
    deliberately omitted (already Disallow'd in robots.txt).
    """
    site = SITE_URL.rstrip("/")
    urls: list[tuple[str, str, str]] = []  # (loc, changefreq, priority)

    urls.append((f"{site}/", "hourly", "1.0"))
    urls.append((f"{site}/gauges.html", "hourly", "0.8"))
    urls.append((f"{site}/map.html", "daily", "0.8"))
    urls.append((f"{site}/custom_gauges.php", "daily", "0.6"))
    for state in states:
        urls.append((f"{site}/{state}.html", "hourly", "0.9"))
    urls.append((f"{site}/about.php", "monthly", "0.4"))
    urls.append((f"{site}/disclaimer.php", "monthly", "0.4"))
    urls.append((f"{site}/privacy.php", "monthly", "0.4"))
    urls.append((f"{site}/contact.php", "monthly", "0.4"))

    for r in reaches:
        urls.append((f"{site}/description.php?id={r.id}", "hourly", "0.7"))

    for gid in session.scalars(select(Gauge.id).order_by(Gauge.id)).all():
        urls.append((f"{site}/gauge.php?id={gid}", "hourly", "0.6"))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, freq, pri in urls:
        lines.append(
            f"  <url><loc>{loc}</loc>"
            f"<changefreq>{freq}</changefreq>"
            f"<priority>{pri}</priority></url>"
        )
    lines.append("</urlset>")
    _atomic_write(output_dir / "sitemap.xml", "\n".join(lines) + "\n")


def _set_acls(directory: Path) -> None:
    """Set POSIX ACLs so www-data can read the deployed directory.

    No-op on systems without setfacl (e.g. macOS dev workstations) — the
    ACLs are a Linux-prod concern and macOS just isn't going to have a
    www-data user anyway.
    """
    import subprocess

    if shutil.which("setfacl") is None:
        logger.debug("setfacl not on PATH — skipping ACL apply on %s", directory)
        return

    subprocess.run(
        ["setfacl", "-R", "-m", "u:www-data:rX", str(directory)],
        check=True,
    )
    subprocess.run(
        ["setfacl", "-R", "-d", "-m", "u:www-data:rX", str(directory)],
        check=True,
    )


def _deploy_staging_to_live(staging: Path, live: Path) -> set[Path]:
    """Copy every regular file in *staging* into *live* via per-file rename.

    For each file under ``staging``:
      1. Ensure the matching parent dir in ``live`` exists.
      2. If a same-content file already exists at the destination, leave
         it alone — preserving the live file's mtime keeps PHP cache-bust
         URLs (filemtime-derived ``?v=…``) stable across rebuilds when
         only metadata churned upstream.
      3. Otherwise ``shutil.copy2`` to ``<live>/<rel>.new`` — preserves
         mode + xattrs (Linux ACLs live in xattrs, so ``u:www-data:rX``
         carries over from a staging tree run through ``_set_acls``).
      4. ``os.replace`` the temp file over the final name — atomic
         rename(2) on the same filesystem.

    Returns the set of relative paths installed, for the orphan sweep.

    ``staging`` and ``live`` must be on the same filesystem. Symlinks and
    empty directories in ``staging`` are skipped — only regular files are
    propagated.
    """
    staging = staging.resolve()
    live = live.resolve()
    kept: set[Path] = set()
    for src in staging.rglob("*"):
        if not src.is_file() or src.is_symlink():
            continue
        rel = src.relative_to(staging)
        dst = live / rel
        kept.add(rel)
        if dst.exists() and not dst.is_symlink() and filecmp.cmp(src, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".new")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    return kept


def _sweep_orphans(live: Path, kept: set[Path]) -> list[Path]:
    """Delete files in *live* whose relpaths aren't in *kept*.

    Called after ``_deploy_staging_to_live`` so files left over from a
    previous build (but not produced by this one) get removed. Empty
    directories are left alone — harmless, and avoids a race with any
    concurrent reader.

    Returns the list of relative paths removed, for the build log.
    """
    live = live.resolve()
    removed: list[Path] = []
    for p in live.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(live)
        if rel not in kept:
            p.unlink()
            removed.append(rel)
    return removed


def build(args: argparse.Namespace) -> None:
    """Generate static HTML/CSV/text files into output_dir.

    Builds to a sibling ``.staging`` directory, applies ACLs, then per-file
    rename-replaces each output into output_dir and sweeps orphans. The
    per-file rename keeps every URL atomic — a request always sees either
    the old or new file, never a half-written one — without ever swapping
    a symlink under in-flight PHP requests.
    """
    output_dir = Path(
        getattr(args, "output_dir", None)
        or os.environ.get("OUTPUT_DIR")
        or str(BASE_DIR / "public_html")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / f"{output_dir.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _build_to_dir(staging, args)
        # Set ACLs on staging so shutil.copy2 carries them via xattrs into
        # each <live>/<file>.new temp, which then rename-replaces the final.
        # Without this, copy2's empty-xattr copy would clobber the inherited
        # default ACL on every deploy and www-data would lose read access.
        _set_acls(staging)
        kept = _deploy_staging_to_live(staging, output_dir)
        removed = _sweep_orphans(output_dir, kept)
        print(
            f"Build complete → {output_dir} ({len(kept)} installed, {len(removed)} orphans removed)"
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _build_and_write(
    session: Session,
    reaches: list[Reach],
    columns: list[dict[str, Any]],
    state: str,
    states: list[str],
    css_link: str,
    output_dir: Path,
    *,
    is_all_page: bool = False,
    preloaded: tuple[set[int], dict[tuple[int, DataType], LatestGaugeObservation]] | None = None,
    filename: str | None = None,
) -> None:
    """Build and write CSV, text, and HTML for a state (or all)."""
    suffix = f"_{state}" if state else ""
    label = state or "all"
    if filename is None:
        filename = f"{state}.html" if state else "all.html"
    title = f"{state} River Levels" if state else "River Levels"

    logger.info("Building %s: %d reaches", label, len(reaches))

    # Pre-load ALL data at gauge level (or reuse preloaded)
    gauge_ids = [r.gauge_id for r in reaches if r.gauge_id]
    if preloaded:
        calculated_gauge_ids, all_latest = preloaded
    else:
        calculated_gauge_ids = get_calculated_gauge_ids(session, gauge_ids)
        all_latest = get_all_latest_gauges(session, gauge_ids)
    sparkline_obs = _select_sparkline_series(session, gauge_ids)

    # CSV
    csv_content = _build_csv(reaches, columns, state, calculated_gauge_ids, all_latest)
    _atomic_write(output_dir / f"levels{suffix}.csv", csv_content)

    # Text
    text_content = _build_text(reaches, columns, state, calculated_gauge_ids, all_latest)
    _atomic_write(output_dir / f"levels{suffix}.text", text_content)

    # HTML — sparklines loaded lazily via JS
    table_html, letters = _build_html_table(
        reaches, columns, calculated_gauge_ids, all_latest, is_all_page=is_all_page
    )
    huc6_names: dict[str, str] = {
        row.code: row.name for row in session.scalars(select(HucName).where(HucName.level == 6))
    }
    filter_data = _collect_filter_data(reaches, calculated_gauge_ids, all_latest, huc6_names)
    filter_bar_html = _build_filter_bar(filter_data, is_all_page=is_all_page)
    page_html = _build_page(
        table_html,
        css_link,
        states,
        state,
        title,
        letters=letters,
        filter_bar_html=filter_bar_html,
        path=f"/{filename}",
    )
    _atomic_write(output_dir / filename, page_html)

    # Sparklines JSON — keyed by gauge_id, loaded by levels.js after paint
    sparklines: dict[str, str] = {}
    for reach in reaches:
        if reach.gauge and reach.gauge.id not in sparklines:
            svg = _build_sparkline(reach, sparkline_obs)
            if svg:
                sparklines[str(reach.gauge.id)] = svg
    static_dir = output_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(static_dir / "sparklines.json", json.dumps(sparklines))
