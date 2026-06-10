"""Generate the static ``site-config.json`` the map JS reads (S3d).

A **non-executable** JSON artifact (fetched, never `<script>`-evaluated): the map's
default extent plus the OSMB-style overlay-layer presentation. ``static/map.js`` and
``static/feature-map.js`` fetch it and build their layer defs + default view from it,
replacing the layer-def constants previously **duplicated** across both files.

The values come from the dataset map config (:mod:`kayak.dataset.map`), which
defaults to the current WKCC/Oregon values — so the built site is byte-identical
until a dataset opts in via ``map.yaml``. The JSON schema and the JS consumers are
unchanged from S3d-1 (which sourced the same values from inline literals).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from kayak.dataset.map import get_map_config


def build_site_config(osmb_url: Callable[[str], str]) -> str:
    """Return the strict-JSON ``site-config.json`` payload (with a trailing newline).

    *osmb_url* maps a layer's GeoJSON output filename → the served
    ``/static/<file>?v=<hash|mtime>`` URL, or ``""`` when the file isn't staged yet
    (the nightly fetch hasn't landed it). Passed in so the build owns file naming and
    cache-busting; the JS treats an empty ``url`` as "no layer to fetch".

    Output is ``sort_keys``-stable so the build is reproducible and the
    no-``map.yaml`` build stays byte-identical to S3d-1.
    """
    cfg = get_map_config()
    layers: list[dict[str, object]] = []
    for spec in cfg.presentation_layers():
        layer = {k: v for k, v in spec.items() if k != "filename"}
        layer["url"] = osmb_url(str(spec["filename"]))
        layers.append(layer)
    payload = {"map": {"center": cfg.center, "zoom": cfg.zoom}, "layers": layers}
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
