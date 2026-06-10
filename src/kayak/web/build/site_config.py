"""Generate the static ``site-config.json`` the map JS reads (S3d).

A **non-executable** JSON artifact (fetched, never `<script>`-evaluated): the map's
default extent plus the OSMB-style overlay-layer presentation. ``static/map.js`` and
``static/feature-map.js`` fetch it and build their layer defs + default view from it,
replacing the layer-def constants previously **duplicated** across both files.

Engine defaults reproduce the current WKCC/Oregon values, so the built site is
byte-identical until a dataset opts in. **S3d-2** moves these literals into
``kayak.dataset.map`` and sources them via ``get_map_config()``; the JSON schema and
the JS consumers do not change between S3d-1 and S3d-2.
"""

from __future__ import annotations

import json
from collections.abc import Callable

# Engine-default map config — the current WKCC/Oregon values, transcribed from the
# (now-removed) ``map.js``/``feature-map.js`` ``OSMB_LAYER_DEFS`` + ``fetch_osmb._LAYERS``
# + the three OSMB landing-URL constants. S3d-2 relocates these to
# ``kayak.dataset.map._DEFAULT_*`` (the source of truth a dataset can override).
_DEFAULT_CENTER: list[float] = [44.0, -120.5]
_DEFAULT_ZOOM: int = 7

# Per layer: presentation (color/shape/size/zIndex/defaultOn) + the popup template key
# (``popup`` → an engine-owned builder in the JS) + the static landing URL the popup
# links to (``popupLink``) + the GeoJSON output filename (resolved to a served URL at
# build time). The popup HTML templates stay engine-owned and escaped.
_DEFAULT_LAYERS: tuple[dict[str, object], ...] = (
    {
        "key": "obstructions",
        "label": "Obstructions",
        "color": "#ff00ff",
        "shape": "triangle",
        "size": 16,
        "zIndex": 200,
        "defaultOn": False,
        "popup": "obstructions",
        "popupLink": "https://geo.maps.arcgis.com/apps/dashboards/59f4dfde321f447b9245a1451c83e054",
        "filename": "osmb-obstructions.geojson",
    },
    {
        "key": "dams",
        "label": "Dams / weirs",
        "color": "#6a1b9a",
        "shape": "diamond",
        "size": 14,
        "zIndex": 100,
        "defaultOn": False,
        "popup": "dams",
        "popupLink": "https://www.oregon.gov/osmb/boating-facilities/Pages/Maps-and-Apps.aspx",
        "filename": "osmb-dams.geojson",
    },
    {
        "key": "access",
        "label": "Access sites",
        "color": "#1b5e20",
        "shape": "circle",
        "size": 5,
        "zIndex": 0,
        "defaultOn": False,
        "popup": "access",
        "popupLink": "https://experience.arcgis.com/experience/72308dd6b893451690a14437cde89be8",
        "filename": "osmb-access-sites.geojson",
    },
)


def build_site_config(osmb_url: Callable[[str], str]) -> str:
    """Return the strict-JSON ``site-config.json`` payload (with a trailing newline).

    *osmb_url* maps a layer's GeoJSON output filename → the served
    ``/static/<file>?v=<hash|mtime>`` URL, or ``""`` when the file isn't staged yet
    (the nightly fetch hasn't landed it). Passed in so the build owns file naming and
    cache-busting; the JS treats an empty ``url`` as "no layer to fetch".

    Output is ``sort_keys``-stable so the build is reproducible and the S3d-1↔S3d-2
    byte-identity invariant is testable.
    """
    layers = [
        {
            "key": spec["key"],
            "label": spec["label"],
            "color": spec["color"],
            "shape": spec["shape"],
            "size": spec["size"],
            "zIndex": spec["zIndex"],
            "defaultOn": spec["defaultOn"],
            "popup": spec["popup"],
            "popupLink": spec["popupLink"],
            "url": osmb_url(str(spec["filename"])),
        }
        for spec in _DEFAULT_LAYERS
    ]
    payload = {"map": {"center": _DEFAULT_CENTER, "zoom": _DEFAULT_ZOOM}, "layers": layers}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
