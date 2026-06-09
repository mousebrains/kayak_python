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
from sqlalchemy.orm import Session, selectinload

from kayak.config import BASE_DIR, OSMB_DIR, SITE_URL
from kayak.db.cache import get_all_latest_gauges
from kayak.db.engine import get_session
from kayak.db.gauges import get_calculated_gauge_ids
from kayak.db.models import DataType, Gauge, HucName, LatestGaugeObservation, Reach
from kayak.db.reaches import all_state_names, reaches_query
from kayak.resources import resource_dir
from kayak.utils.pubhash import encode as pubhash_encode
from kayak.web.build._shared import (
    _CSS_PATH,
    _FILTERS_JS_PATH,
    _JS_PATH,
    _LICENSE_META,
    _NAV_STATES,
    _STATIC_DIR,
    _atomic_write,
    _css_link_tag,
    _load_css,
)
from kayak.web.build.gauges import _write_gauges_page
from kayak.web.build.geojson import (
    _build_gauges_state,
    _build_gauges_static,
    _build_reaches_state,
    _build_reaches_static,
)
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
from kayak.web.regression import (
    UnsafeContentError,
    render_markdown_safe,
    validate_json_sidecar,
    validate_svg,
)

logger = logging.getLogger(__name__)


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'build' subcommand."""
    parser = subparsers.add_parser("build", help="Generate static HTML files to output directory")
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", str(BASE_DIR / "public_html")),
        help="Output directory (default: $OUTPUT_DIR or public_html/)",
    )
    parser.set_defaults(func=build)


def _osmb_url(static_dir: Path, filename: str) -> str:
    """Build a /static/<file>?v=<mtime> URL, or "" if the file isn't there.

    Source-of-truth for mtime is the staged copy under ``static_dir``;
    ``shutil.copy2`` (called from ``_deploy_static_assets``) preserves
    the upstream mtime from the OSMB staging dir (``OSMB_DIR``, where
    ``levels fetch-osmb`` wrote the file), and the per-file rename in
    ``_deploy_staging_to_live`` skips identical content, so the live
    file's mtime stays put across no-op nightly fetches.
    """
    path = static_dir / filename
    try:
        mtime = int(path.stat().st_mtime)
    except FileNotFoundError:
        return ""
    return f"/static/{filename}?v={mtime}"


# Build-PROCESSED assets that ship in the packaged web/static dir but are
# emitted to the output as hashed/versioned variants by ``_build_to_dir``
# (style-<hash>.css at output root + /static; levels.js/filters.js with
# ?v=mtime), so the as-is copy below skips them to avoid an unreferenced
# unhashed duplicate under /static.
_BUILD_PROCESSED_STATIC: frozenset[str] = frozenset({"style.css", "levels.js", "filters.js"})


def _deploy_static_assets(output_dir: Path) -> None:
    """Copy committed + generated static assets into ``output_dir/static/``.

    Two sources, both outside the deploy target:

    * the packaged ``web/static/`` tree (committed source assets — map.js,
      leaflet, images, manifest, sw.js, …), resolved via the package so a
      wheel install finds them (S4a-2 slice B1). The build-processed trio
      (``style.css``/``levels.js``/``filters.js``) is skipped here — it is
      emitted as hashed/versioned variants by ``_build_to_dir``.
    * the OSMB staging dir (``config.osmb_dir``) — ``*.geojson`` written by
      ``levels fetch-osmb`` on its own cadence. Copying them in here puts
      them in the build's ``kept`` set so ``_sweep_orphans`` preserves them
      and ``_osmb_url`` resolves their ``?v=mtime`` URLs.

    ``sw.js`` lands at the output root (not under ``static/``) so the service
    worker controls scope ``/``. Directories (``images/``) propagate via
    ``copytree`` with ``dirs_exist_ok=True``.

    Also publishes the regression-analysis artifacts: ``docs/regression/*.{svg,json}``
    pass through verbatim into ``static/regression/`` for use by PHP
    gauge_detail.php, and each ``docs/regression/*.md`` writeup is
    rendered to a self-contained HTML page under the same directory so
    the "Full analysis →" link works without exposing the repo.
    """
    static_dir = output_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    for path in _STATIC_DIR.iterdir():
        if path.is_file():
            if path.name in _BUILD_PROCESSED_STATIC:
                continue
            dst = output_dir if path.name == "sw.js" else static_dir
            shutil.copy2(path, dst / path.name)
        elif path.is_dir():
            shutil.copytree(path, static_dir / path.name, dirs_exist_ok=True)
    # Generated OSMB overlays staged outside the package by `levels fetch-osmb`.
    if OSMB_DIR.is_dir():
        for path in OSMB_DIR.glob("*.geojson"):
            shutil.copy2(path, static_dir / path.name)
    _deploy_regression_artifacts(static_dir)


def _deploy_regression_artifacts(static_dir: Path) -> None:
    """Render + sanitize ``docs/regression/*`` into ``static_dir/regression/``.

    The .md/.svg/.json files are dataset-authored output served to anonymous
    users, so the engine does not trust them (see :mod:`kayak.web.regression`):
    Markdown is rendered and sanitized with an nh3 allowlist, SVG is validated
    against a strict allowlist and **re-serialized** (never copied verbatim), and
    JSON sidecars are shape-checked. A nonconforming artifact fails the build
    (fail-closed) — ``levels validate-dataset`` catches it earlier in the deploy.
    The markdown is pre-rendered to HTML because the kayak repo is private (a
    ``github.com/…md`` link would 404 for end users).
    """
    regression_src = BASE_DIR / "docs" / "regression"
    if not regression_src.is_dir():
        return
    dst = static_dir / "regression"
    dst.mkdir(parents=True, exist_ok=True)
    # SVG plot: validate + re-serialize from the parsed tree (never serve verbatim).
    for path in sorted(regression_src.glob("*.svg")):
        try:
            safe_svg = validate_svg(path.read_text(encoding="utf-8"))
        except UnsafeContentError as exc:
            raise UnsafeContentError(f"{path.name}: {exc}") from exc
        (dst / path.name).write_text(safe_svg, encoding="utf-8")
    # JSON fact-box: validate, then copy verbatim (data, not active content).
    for path in sorted(regression_src.glob("*.json")):
        try:
            validate_json_sidecar(path.read_text(encoding="utf-8"))
        except UnsafeContentError as exc:
            raise UnsafeContentError(f"{path.name}: {exc}") from exc
        shutil.copy2(path, dst / path.name)
    # Render each Markdown writeup to sanitized, kayak-styled standalone HTML. The
    # README.md sits alongside slug docs but is for repo maintainers — skip it.
    for path in sorted(regression_src.glob("*.md")):
        if path.stem.lower() == "readme":
            continue
        html_body = render_markdown_safe(path.read_text(encoding="utf-8"))
        title = path.stem.replace("_", " ")
        html = (
            f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f"<title>{title} — Regression analysis</title>"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<link rel="stylesheet" href="/style.css">'
            "<style>main.regression-doc{max-width:920px;margin:1.5rem auto;padding:0 1rem}"
            "main.regression-doc img{max-width:100%;height:auto}"
            "main.regression-doc table{border-collapse:collapse;margin:.5rem 0}"
            "main.regression-doc th,main.regression-doc td{border:1px solid var(--c-border);padding:4px 8px}"
            "main.regression-doc pre{background:var(--c-stripe);padding:.6rem;overflow-x:auto;font-size:.85rem}"
            "main.regression-doc code{background:var(--c-stripe);padding:0 .2rem;border-radius:2px}"
            "main.regression-doc nav.crumbs{margin-bottom:1rem;color:var(--c-text-muted);font-size:.9rem}"
            "</style></head><body>"
            f'<main class="regression-doc">'
            f'<nav class="crumbs"><a href="/index.html">← Back to river levels</a></nav>'
            f"{html_body}"
            "</main></body></html>\n"
        )
        (dst / f"{path.stem}.html").write_text(html)


def _deploy_php_files(output_dir: Path) -> None:
    """Install the PHP layer: top-level pages, ``includes/``, ``_internal/``,
    and ``style.css``.

    ``style.css`` lives at the output root because ``src/kayak/web/php/header.php`` reads
    it via ``__DIR__/../style.css`` — the hashed copy under ``static/`` is
    the cacheable variant served to static-HTML clients.

    The PHP layer ships inside the package at ``src/kayak/web/php`` (S4a-2
    slice B2), resolved via ``resource_dir`` so a wheel install finds it.
    """
    php_dir = resource_dir("web", "php")
    for path in php_dir.iterdir():
        if path.is_file() and path.suffix == ".php":
            shutil.copy2(path, output_dir / path.name)

    includes_dir = output_dir / "includes"
    includes_dir.mkdir(parents=True, exist_ok=True)
    for path in (php_dir / "includes").iterdir():
        if path.is_file():
            shutil.copy2(path, includes_dir / path.name)

    # _internal/ — maintainer-only dashboard. Mirror the includes/ pattern:
    # a single flat dir; deeper structure can be added later if the
    # dashboard grows into multiple PHP files. nginx only routes
    # `/_internal/` and `/_internal/index.php` on the canonical
    # levels.wkcc.org vhost (the mousebrains vhost holds a 404 guard)
    # (docs/done/PLAN_internal_dashboard.md Phase 2.4).
    internal_dir = output_dir / "_internal"
    internal_dir.mkdir(parents=True, exist_ok=True)
    for path in (php_dir / "_internal").iterdir():
        if path.is_file():
            shutil.copy2(path, internal_dir / path.name)

    shutil.copy2(_CSS_PATH, output_dir / "style.css")


def _deploy_config_files(output_dir: Path) -> None:
    """Copy the install templates (``404.html``, ``robots.txt``) into the output root.

    These ship inside the package at ``src/kayak/web/install-templates`` (S4a-2
    slice B2), resolved via ``resource_dir`` so a wheel install finds them. Only
    present files are copied — the rest of the output dir is the deploy target,
    populated by the generated content path. (No ``.htaccess`` — the site is
    nginx-served; a club switching to Apache would add one to the templates dir.)
    """
    templates_dir = resource_dir("web", "install-templates")
    for name in ("404.html", "robots.txt"):
        src = templates_dir / name
        if src.is_file():
            shutil.copy2(src, output_dir / name)


def _deploy_license_files(output_dir: Path) -> None:
    """Copy LICENSE (GPL v3) and LICENSE-DATA (CC BY-NC 4.0) into the output.

    Packaged copies ship inside the wheel at ``src/kayak/web/legal/*.txt``
    (S4a-2 slice B2; the repo-root ``LICENSE``/``LICENSE-DATA`` stay for the
    GitHub/pyproject convention, and a test guards the two against drift),
    resolved via ``resource_dir`` so a wheel install finds them. Served as
    ``.txt`` so nginx returns ``text/plain`` and browsers render them inline;
    the footer in ``src/kayak/web/php/includes/footer.php`` and the static-page
    footer link to ``/LICENSE.txt`` and ``/LICENSE-DATA.txt``.
    """
    legal_dir = resource_dir("web", "legal")
    for name in ("LICENSE.txt", "LICENSE-DATA.txt"):
        src = legal_dir / name
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
    _deploy_license_files(output_dir)


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

        # Pre-load data for all reaches at gauge level. all_latest covers
        # every gauge with an observation (not just reach-linked ones) so
        # the gauges-state.json build later can read orphan-gauge readings
        # too — reach-side consumers only look up reach-linked keys so the
        # broader load is a strict superset, no behavior change for them.
        gauge_ids = [r.gauge_id for r in all_reaches if r.gauge_id]
        calculated_gauge_ids = get_calculated_gauge_ids(session, gauge_ids)
        all_gauge_ids_with_obs = list(
            session.scalars(select(LatestGaugeObservation.gauge_id).distinct())
        )
        all_latest = get_all_latest_gauges(session, all_gauge_ids_with_obs)
        # All gauges with a (lat, lon) for the map's gauge layer; reaches
        # eager-loaded so _gauge_status_from_reaches doesn't N+1.
        all_gauges = list(
            session.scalars(
                select(Gauge)
                .options(selectinload(Gauge.reaches))
                .where(Gauge.latitude.is_not(None), Gauge.longitude.is_not(None))
            )
        )

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
        # Same split for the gauge layer (Item 2 of map_and_ui_tweaks).
        # Static geometry + metadata is long-cached and content-hashed;
        # state (status + readings + staleness) refreshes hourly.
        gauges_geom_json = _build_gauges_static(all_gauges)
        gauges_state_json = _build_gauges_state(all_gauges, calculated_gauge_ids, all_latest)
        gauges_geom_hash = hashlib.sha256(gauges_geom_json.encode()).hexdigest()[:10]
        _atomic_write(static_dir / "gauges-geom.json", gauges_geom_json)
        _atomic_write(static_dir / "gauges-state.json", gauges_state_json)
        logger.info(
            "reaches-geom.json: %d bytes; reaches-state.json: %d bytes; "
            "gauges-geom.json: %d bytes; gauges-state.json: %d bytes",
            len(static_json),
            len(state_json),
            len(gauges_geom_json),
            len(gauges_state_json),
        )
        # Drop the retired combined file if an older build left one behind.
        with suppress(FileNotFoundError):
            (static_dir / "reaches.geojson").unlink()

        geom_url = f"/static/reaches-geom.json?v={geom_hash}"
        state_url = "/static/reaches-state.json"
        gauges_geom_url = f"/static/gauges-geom.json?v={gauges_geom_hash}"
        gauges_state_url = "/static/gauges-state.json"
        # OSMB overlays: empty string when the nightly fetch hasn't landed
        # the file yet — map.js treats absent attrs as "no layer to fetch".
        osmb_obstructions_url = _osmb_url(static_dir, "osmb-obstructions.geojson")
        osmb_dams_url = _osmb_url(static_dir, "osmb-dams.geojson")
        osmb_access_url = _osmb_url(static_dir, "osmb-access-sites.geojson")
        map_html = _build_map_page(
            css_link,
            states,
            geom_url,
            state_url,
            gauges_geom_url,
            gauges_state_url,
            osmb_obstructions_url,
            osmb_dams_url,
            osmb_access_url,
        )
        _atomic_write(output_dir / "map.html", map_html)

        # index.html = all reaches levels table (excludes map_only). Data
        # spans every state, so this is the "all page" that gets the state
        # filter group in the filter bar. state="" keeps the nav bar with
        # no state highlighted and the title as plain "River Levels".
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

        # gauges.html — supplemental all-gauges listing. all_latest (loaded
        # above with the full all_gauge_ids_with_obs set) already covers
        # every gauge with observations including orphans, so reuse it
        # rather than re-querying. State-scoped filtered views are served
        # via /gauges.html#st=<state> (filters.js honors the fragment),
        # so no per-state gauges.<slug>.html artifact is generated.
        _write_gauges_page(session, all_latest, states, css_link, output_dir)

        # Per-state landing pages — generated for every state in
        # _NAV_STATES, independent of reach presence. Montana has no
        # reaches yet but does have gauges, so the landing page exists
        # and links to the filtered /gauges.html view; reach-related
        # anchors are suppressed inside _build_placeholder_page when
        # the state has no entry in `states` (the reach-states list).
        for state in sorted(_NAV_STATES):
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

    Includes the index, each state's landing page, the gauges/map listings,
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
    # State landing pages exist for every _NAV_STATES entry — including
    # gauges-only states like Montana — so list all of them, not just
    # the reach-states.
    for state in sorted(_NAV_STATES):
        urls.append((f"{site}/{state}.html", "hourly", "0.9"))
    urls.append((f"{site}/about.php", "monthly", "0.4"))
    urls.append((f"{site}/disclaimer.php", "monthly", "0.4"))
    urls.append((f"{site}/privacy.php", "monthly", "0.4"))
    urls.append((f"{site}/contact.php", "monthly", "0.4"))

    for r in reaches:
        urls.append((f"{site}/description.php?h={pubhash_encode(r.id)}", "hourly", "0.7"))

    for gid in session.scalars(select(Gauge.id).order_by(Gauge.id)).all():
        urls.append((f"{site}/gauge.php?h={pubhash_encode(gid)}", "hourly", "0.6"))

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

    Skips paths under ``.staging`` (the in-progress build tree, which
    lives as a subdir of output_dir per QW.6 — see ``build()`` docstring).
    Otherwise the sweep would delete every staged file the current build
    just wrote.

    Returns the list of relative paths removed, for the build log.
    """
    live = live.resolve()
    removed: list[Path] = []
    for p in live.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(live)
        if rel.parts and rel.parts[0] == _STAGING_DIRNAME:
            continue
        if rel not in kept:
            p.unlink()
            removed.append(rel)
    return removed


_STAGING_DIRNAME = ".staging"


def build(args: argparse.Namespace) -> None:
    """Generate static HTML files into output_dir.

    Builds to a ``.staging`` subdirectory of output_dir, applies ACLs, then
    per-file rename-replaces each output into output_dir and sweeps orphans.
    The per-file rename keeps every URL atomic — a request always sees
    either the old or new file, never a half-written one — without ever
    swapping a symlink under in-flight PHP requests.

    Why ``.staging`` is a subdir, not a sibling: per QW.6 (audit follow-up
    plan), kayak-pipeline.service narrows ``ReadWritePaths`` to specific
    subdirs. systemd validates every path exists at namespace-setup time,
    so a sibling ``public_html.staging`` (which is rmtree'd between runs)
    fails the check. A subdir lives inside output_dir, which always exists.
    nginx's ``location ~ /\\.`` dotfile rule blocks ``/.staging/*`` access,
    so this dir is invisible to clients.
    """
    output_dir = Path(
        getattr(args, "output_dir", None)
        or os.environ.get("OUTPUT_DIR")
        or str(BASE_DIR / "public_html")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / _STAGING_DIRNAME
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
    """Build and write the HTML page + sparklines for a state (or all)."""
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

    # Sparklines JSON — keyed by gauge_id, loaded by levels.js after paint.
    # _meta carries the data license; the JS consumer at
    # src/kayak/web/static/levels.js does keyed lookup by data-gid only, so
    # adding extra top-level keys is non-breaking.
    sparklines: dict[str, str] = {}
    for reach in reaches:
        if reach.gauge and reach.gauge.id not in sparklines:
            svg = _build_sparkline(reach, sparkline_obs)
            if svg:
                sparklines[str(reach.gauge.id)] = svg
    static_dir = output_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    sparklines_out: dict[str, Any] = {"_meta": _LICENSE_META, **sparklines}
    _atomic_write(static_dir / "sparklines.json", json.dumps(sparklines_out))
