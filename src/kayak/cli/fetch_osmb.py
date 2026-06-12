"""Fetch configured map overlay GeoJSON layers.

Pulls the dataset map config's ArcGIS Feature Service layers as GeoJSON and writes
them to the configured map-layer staging dir (``MAP_LAYERS_DIR`` preferred;
``OSMB_DIR`` still honored), where ``levels build`` picks them up via
``_deploy_static_assets`` and copies them into ``OUTPUT_DIR/static``. The legacy
``fetch-osmb`` command remains as a compatibility alias for existing systemd
units. The engine default has no overlay layers; a dataset supplies layers with
``DATASET_DIR/map.yaml``. The staging dir is kept outside the package (generated
runtime data, not an engine resource — S4a-2 slice B1). Files are atomic-replaced
only when the content changed, so an unchanged response preserves the file's mtime
— that mtime feeds the ``?v=<mtime>`` cache-bust URLs on map.html, so the browser
cache stays warm across nightly no-op runs.

Run nightly (see ``systemd/kayak-fetch-osmb.{service,timer}``); the
data updates rarely.
"""

import argparse
import json
import logging
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import cast

from kayak.config import MAP_LAYERS_DIR
from kayak.dataset.map import get_map_config
from kayak.utils.http_client import fetch as http_fetch
from kayak.web.build._shared import _atomic_write_bytes

logger = logging.getLogger(__name__)


BBox = tuple[float, float, float, float]

# Defensive cap on pagination — bigger than any layer we expect from
# OSMB so we never tail-spin if the server keeps returning full pages.
_MAX_PAGES = 50


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register map-layer fetch subcommands."""
    _add_parser(
        subparsers,
        "fetch-map-layers",
        "Fetch configured map overlay GeoJSON to the map-layer staging dir",
        fetch_map_layers,
    )
    _add_parser(
        subparsers,
        "fetch-osmb",
        "Compatibility alias for fetch-map-layers",
        fetch_osmb,
    )


def _add_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    name: str,
    help_text: str,
    handler: Callable[[argparse.Namespace], None],
) -> None:
    parser = subparsers.add_parser(
        name,
        help=help_text,
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write GeoJSON files (default: the configured MAP_LAYERS_DIR)",
    )
    parser.set_defaults(func=handler)


def fetch_map_layers(args: argparse.Namespace) -> None:
    """Fetch configured map layers. Exits non-zero if every configured layer fails."""
    _fetch_configured_layers(args, command_name="fetch-map-layers")


def fetch_osmb(args: argparse.Namespace) -> None:
    """Compatibility wrapper for ``levels fetch-osmb``."""
    _fetch_configured_layers(args, command_name="fetch-osmb")


def _fetch_configured_layers(args: argparse.Namespace, *, command_name: str) -> None:
    output_dir = Path(args.output_dir) if args.output_dir else MAP_LAYERS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = get_map_config()
    layers = cfg.fetch_layers()
    if not layers:
        logger.info("%s: no map overlay layers configured", command_name)
        return
    bbox = cast(BBox, tuple(cfg.bbox))

    successes = 0
    for filename, base_url, out_fields in layers:
        try:
            body, feature_count = _fetch_all_pages(base_url, out_fields, bbox)
        except Exception as exc:
            logger.error("%s: %s failed: %s", command_name, filename, exc)
            continue

        dst = output_dir / filename
        changed = _write_if_changed(dst, body)
        logger.info(
            "%s: %s — %d features, %s",
            command_name,
            filename,
            feature_count,
            "updated" if changed else "unchanged",
        )
        successes += 1

    if successes == 0:
        raise SystemExit(f"{command_name}: every configured layer failed; see logs")


def _fetch_all_pages(base_url: str, out_fields: tuple[str, ...], bbox: BBox) -> tuple[bytes, int]:
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
        # Page-size check uses the raw (pre-filter) count so the
        # "shorter than first page = last page" termination still
        # works when intermediate pages happen to be mostly junk.
        page_count = len(features)
        all_features.extend(f for f in features if _in_bbox(f, bbox))
        if first_page_size is None:
            first_page_size = page_count
        if not page_count or page_count < first_page_size:
            break
        offset += page_count
    else:
        raise RuntimeError(f"pagination hit {_MAX_PAGES} pages without terminating")

    merged = {"type": "FeatureCollection", "features": all_features}
    body = json.dumps(merged, separators=(",", ":"), sort_keys=True).encode()
    return body, len(all_features)


def _in_bbox(feature: dict, bbox: BBox) -> bool:
    """True if *feature* is a Point inside the configured bbox; drops malformed too."""
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return False
    lon, lat = coords[0], coords[1]
    w, s, e, n = bbox
    return bool(w <= lon <= e and s <= lat <= n)


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
