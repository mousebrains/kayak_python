"""Fetch OSMB (Oregon State Marine Board) hazard + access GeoJSON layers.

Pulls three public AGOL feature services as GeoJSON and writes them to
``BASE_DIR/static/``, where ``levels build`` picks them up via
``_deploy_static_assets``. Files are atomic-replaced only when the
content changed, so an unchanged response preserves the file's mtime —
that mtime feeds the ``?v=<mtime>`` cache-bust URLs on map.html, so the
browser cache stays warm across nightly no-op runs.

Run nightly (see ``systemd/kayak-fetch-osmb.{service,timer}``); the
data updates rarely.
"""

import argparse
import json
import logging
import urllib.parse
from pathlib import Path

from kayak.config import BASE_DIR
from kayak.utils.http_client import fetch as http_fetch
from kayak.web.build._shared import _atomic_write_bytes

logger = logging.getLogger(__name__)


# Base URLs kept un-parameterized so they stay grep-able against AGOL's REST
# catalog. ``out_fields`` matches what the corresponding popup formatter in
# static/map.js reads — anything else would just bloat the served GeoJSON.
_LAYERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "osmb-obstructions.geojson",
        "https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services/BORT_Public_View/FeatureServer/0",
        ("waterbody", "waterbodysec", "obslocation", "obsdescript", "recordtime"),
    ),
    (
        "osmb-dams.geojson",
        "https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services/service_d258e7b477f546d0917e868b1330ab3c/FeatureServer/0",
        ("damname", "waterbody", "damheight", "damwidth", "portagedesc", "navigate"),
    ),
    (
        "osmb-access-sites.geojson",
        "https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services/Boating_Access_Sites_OA/FeatureServer/0",
        ("name", "waterway_name", "facility_type", "launch_type", "web_url"),
    ),
)

# Defensive cap on pagination — bigger than any layer we expect from
# OSMB so we never tail-spin if the server keeps returning full pages.
_MAX_PAGES = 50


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'fetch-osmb' subcommand."""
    parser = subparsers.add_parser(
        "fetch-osmb",
        help="Fetch Oregon SMB boating obstruction/dam/access GeoJSON to static/",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write GeoJSON files (default: BASE_DIR/static)",
    )
    parser.set_defaults(func=fetch_osmb)


def fetch_osmb(args: argparse.Namespace) -> None:
    """Fetch OSMB layers. Exits non-zero if every layer fails."""
    output_dir = Path(args.output_dir) if args.output_dir else (BASE_DIR / "static")
    output_dir.mkdir(parents=True, exist_ok=True)

    successes = 0
    for filename, base_url, out_fields in _LAYERS:
        try:
            body, feature_count = _fetch_all_pages(base_url, out_fields)
        except Exception as exc:
            logger.error("fetch-osmb: %s failed: %s", filename, exc)
            continue

        dst = output_dir / filename
        changed = _write_if_changed(dst, body)
        logger.info(
            "fetch-osmb: %s — %d features, %s",
            filename,
            feature_count,
            "updated" if changed else "unchanged",
        )
        successes += 1

    if successes == 0:
        raise SystemExit("fetch-osmb: every layer failed; see logs")


def _fetch_all_pages(base_url: str, out_fields: tuple[str, ...]) -> tuple[bytes, int]:
    """Fetch every page of *base_url* and return a merged FeatureCollection.

    AGOL caps each response at the service's maxRecordCount. The page
    size is inferred from the first response — any subsequent page
    shorter than that is the last one.

    Output bytes use stable key ordering so the byte-equal comparison
    in ``_write_if_changed`` is robust across unchanged upstream data.
    """
    all_features: list[dict] = []
    first_page_size: int | None = None
    offset = 0
    for _ in range(_MAX_PAGES):
        url = _query_url(base_url, out_fields, offset)
        raw = _fetch_page(url)
        try:
            page = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON at offset {offset}: {exc}") from exc
        if not isinstance(page, dict) or page.get("type") != "FeatureCollection":
            raise RuntimeError(f"not a FeatureCollection at offset {offset}")
        features = page.get("features", [])
        all_features.extend(features)
        if first_page_size is None:
            first_page_size = len(features)
        if not features or len(features) < first_page_size:
            break
        offset += len(features)
    else:
        raise RuntimeError(f"pagination hit {_MAX_PAGES} pages without terminating")

    merged = {"type": "FeatureCollection", "features": all_features}
    body = json.dumps(merged, separators=(",", ":"), sort_keys=True).encode()
    return body, len(all_features)


def _query_url(base_url: str, out_fields: tuple[str, ...], offset: int) -> str:
    params = {
        "where": "1=1",
        "outFields": ",".join(out_fields),
        "f": "geojson",
        "resultOffset": str(offset),
    }
    return f"{base_url}/query?{urllib.parse.urlencode(params)}"


def _fetch_page(url: str) -> bytes:
    """Fetch one page via the shared HTTP client (retries + UA + URL validation)."""
    result = http_fetch(url)
    if not result.ok:
        raise RuntimeError(f"transport error: {result.error}")
    if result.status_code >= 400:
        raise RuntimeError(f"HTTP {result.status_code}")
    return result.content


def _write_if_changed(path: Path, content: bytes) -> bool:
    """Atomic-replace *path* with *content* only when bytes differ.

    Returns True if the file was written, False if it matched. Preserving
    mtime on unchanged content keeps the ``?v=<mtime>`` cache-bust URL
    stable across no-op runs.
    """
    try:
        if path.read_bytes() == content:
            return False
    except OSError:
        pass  # missing or unreadable — fall through to write
    _atomic_write_bytes(path, content)
    return True
