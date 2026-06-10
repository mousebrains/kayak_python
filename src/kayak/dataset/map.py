"""Dataset-owned map config (``map.yaml``) — S3d.

The interactive map's default extent + the OSMB-style overlay layers (presentation
+ ArcGIS fetch params). A club supplies its own via an opt-in ``map.yaml`` at the
dataset root (``DATASET_DIR``); resolution is *engine defaults < dataset
``map.yaml``* (a present file fully defines the config — replace, not merge). The
map analogue of :mod:`kayak.dataset.site` / :mod:`kayak.dataset.region`.

S3d consumers read this config:
  - :mod:`kayak.web.build.site_config` renders the presentation half into the
    generated ``static/site-config.json`` the map JS fetches
    (:meth:`MapConfig.presentation_layers`).
  - :mod:`kayak.cli.fetch_osmb` reads the ArcGIS fetch half
    (:meth:`MapConfig.fetch_layers`): endpoint + out_fields + output filename,
    filtered to :attr:`MapConfig.bbox`.
  - :mod:`kayak.cli.validate_dataset` applies the same fail-closed validation when
    ``map.yaml`` is present.

**Security boundary:** the dataset supplies layer presentation, the popup *template
key* + *link*, the ArcGIS endpoint, out_fields, output filename, and the bbox —
NOT popup HTML/templates or arbitrary ArcGIS query text (engine-owned). Every value
renders into HTML / an ``href`` / a fetched URL, and the map JS's sinks are not all
quote-safe, so each value is validated to a safe shape **here** (fail-closed) — this
model, not the JS, is the guarantee — and ``levels validate-dataset`` runs the same
validation at the deploy gate. Engine defaults are the current WKCC/Oregon values,
so a dataset without ``map.yaml`` builds and fetches identically. The generic-default
flip is deferred to S3i.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

# Reuse the region module's HTML-safe label + http(s)-URL validators (the popup
# link must be a safe URL because ``esc()`` does not escape quotes in an href, and
# the label renders as HTML in the layer control) — same shapes site/region use.
from kayak.dataset.region import _safe_http_url, _safe_label

MAP_YAML = "map.yaml"

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Layer key → DOM id / URL-hash token / object key; output filename → served path;
# out_fields → ArcGIS query field names. Keep all to safe, injection-proof charsets.
_KEY_RE = re.compile(r"^[a-z0-9_]+$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.geojson$")
_OUT_FIELD_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SHAPES = ("triangle", "diamond", "circle")
# Popup template keys — each maps to an engine-owned popup builder in the map JS.
_POPUPS = ("obstructions", "dams", "access")


def _hex_color(v: str) -> str:
    if not _HEX_COLOR_RE.match(v):
        raise ValueError(f"color must be a #rrggbb hex color (got {v!r})")
    return v


class MapLayer(BaseModel):
    """One OSMB-style overlay: presentation + popup template + ArcGIS fetch params."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    color: str
    shape: str
    size: int
    z_index: int = 0
    default_on: bool = False
    popup: str
    popup_link: str
    output_filename: str
    endpoint: str
    out_fields: list[str] = []

    _v_label = field_validator("label")(classmethod(lambda cls, v: _safe_label(v)))
    _v_color = field_validator("color")(classmethod(lambda cls, v: _hex_color(v)))
    _v_link = field_validator("popup_link")(classmethod(lambda cls, v: _safe_http_url(v)))
    _v_endpoint = field_validator("endpoint")(classmethod(lambda cls, v: _safe_http_url(v)))

    @field_validator("key")
    @classmethod
    def _v_key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError(f"key must match [a-z0-9_]+ (got {v!r})")
        return v

    @field_validator("shape")
    @classmethod
    def _v_shape(cls, v: str) -> str:
        if v not in _SHAPES:
            raise ValueError(f"shape must be one of {_SHAPES} (got {v!r})")
        return v

    @field_validator("popup")
    @classmethod
    def _v_popup(cls, v: str) -> str:
        if v not in _POPUPS:
            raise ValueError(f"popup must be one of {_POPUPS} (got {v!r})")
        return v

    @field_validator("size")
    @classmethod
    def _v_size(cls, v: int) -> int:
        if not 1 <= v <= 64:
            raise ValueError(f"size must be 1..64 (got {v})")
        return v

    @field_validator("output_filename")
    @classmethod
    def _v_filename(cls, v: str) -> str:
        if not _FILENAME_RE.match(v):
            raise ValueError(f"output_filename must match [A-Za-z0-9._-]+.geojson (got {v!r})")
        return v

    @field_validator("out_fields")
    @classmethod
    def _v_out_fields(cls, v: list[str]) -> list[str]:
        for f in v:
            if not _OUT_FIELD_RE.match(f):
                raise ValueError(f"out_field must match [A-Za-z0-9_]+ (got {f!r})")
        return v


class MapConfig(BaseModel):
    """Typed map config. Engine defaults are the current WKCC/Oregon values (S3d)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    center: list[float] = [44.0, -120.5]
    zoom: int = 7
    bbox: list[float] = [-124.7, 41.9, -116.4, 46.3]
    layers: list[MapLayer] = []

    @field_validator("center")
    @classmethod
    def _v_center(cls, v: list[float]) -> list[float]:
        if len(v) != 2:
            raise ValueError("center must be [lat, lon]")
        lat, lon = v
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError(f"center out of range (got {v})")
        return v

    @field_validator("zoom")
    @classmethod
    def _v_zoom(cls, v: int) -> int:
        if not 0 <= v <= 19:
            raise ValueError(f"zoom must be 0..19 (got {v})")
        return v

    @field_validator("bbox")
    @classmethod
    def _v_bbox(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            raise ValueError("bbox must be [west, south, east, north]")
        w, s, e, n = v
        if not -180 <= w < e <= 180 or not -90 <= s < n <= 90:
            raise ValueError(f"bbox must be ordered W<E, S<N and in range (got {v})")
        return v

    @field_validator("layers")
    @classmethod
    def _v_unique_layer_ids(cls, v: list[MapLayer]) -> list[MapLayer]:
        keys = [layer.key for layer in v]
        key_dupes = sorted({k for k in keys if keys.count(k) > 1})
        if key_dupes:
            raise ValueError(f"duplicate layer key(s): {key_dupes}")
        filenames = [layer.output_filename for layer in v]
        filename_dupes = sorted({f for f in filenames if filenames.count(f) > 1})
        if filename_dupes:
            raise ValueError(f"duplicate layer output_filename(s): {filename_dupes}")
        return v

    def presentation_layers(self) -> list[dict[str, object]]:
        """Presentation half for ``site-config.json`` (build resolves ``filename`` → URL).

        Returns plain dicts keyed exactly as the JSON expects, plus ``filename`` for
        the build's URL resolver to consume + drop.
        """
        return [
            {
                "key": layer.key,
                "label": layer.label,
                "color": layer.color,
                "shape": layer.shape,
                "size": layer.size,
                "zIndex": layer.z_index,
                "defaultOn": layer.default_on,
                "popup": layer.popup,
                "popupLink": layer.popup_link,
                "filename": layer.output_filename,
            }
            for layer in self.layers
        ]

    def fetch_layers(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """ArcGIS fetch half for ``fetch-osmb``: ``(output_filename, endpoint, out_fields)``."""
        return [
            (layer.output_filename, layer.endpoint, tuple(layer.out_fields))
            for layer in self.layers
        ]


# --------------------------------------------------------------------------- #
# Engine defaults — the current WKCC/Oregon map config. The presentation half was
# moved here from web/build/site_config.py (S3d-1). The fetch half was transcribed
# from cli/fetch_osmb.py's former _LAYERS / _OREGON_BBOX table, so the default live
# fetch stays identical until the WKCC dataset carries map.yaml and S3i
# genericizes these defaults.
# --------------------------------------------------------------------------- #

_DEFAULT_CENTER: list[float] = [44.0, -120.5]
_DEFAULT_ZOOM = 7
_DEFAULT_BBOX: list[float] = [-124.7, 41.9, -116.4, 46.3]
_ARCGIS = "https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services"

_DEFAULT_LAYERS: tuple[dict[str, Any], ...] = (
    {
        "key": "obstructions",
        "label": "Obstructions",
        "color": "#ff00ff",
        "shape": "triangle",
        "size": 16,
        "z_index": 200,
        "default_on": False,
        "popup": "obstructions",
        "popup_link": "https://geo.maps.arcgis.com/apps/dashboards/59f4dfde321f447b9245a1451c83e054",
        "output_filename": "osmb-obstructions.geojson",
        "endpoint": f"{_ARCGIS}/BORT_Public_View/FeatureServer/0",
        "out_fields": ["waterbody", "waterbodysec", "obslocation", "obsdescript", "recordtime"],
    },
    {
        "key": "dams",
        "label": "Dams / weirs",
        "color": "#6a1b9a",
        "shape": "diamond",
        "size": 14,
        "z_index": 100,
        "default_on": False,
        "popup": "dams",
        "popup_link": "https://www.oregon.gov/osmb/boating-facilities/Pages/Maps-and-Apps.aspx",
        "output_filename": "osmb-dams.geojson",
        "endpoint": f"{_ARCGIS}/service_d258e7b477f546d0917e868b1330ab3c/FeatureServer/0",
        "out_fields": ["damname", "waterbody", "damheight", "damwidth", "portagedesc", "navigate"],
    },
    {
        "key": "access",
        "label": "Access sites",
        "color": "#1b5e20",
        "shape": "circle",
        "size": 5,
        "z_index": 0,
        "default_on": False,
        "popup": "access",
        "popup_link": "https://experience.arcgis.com/experience/72308dd6b893451690a14437cde89be8",
        "output_filename": "osmb-access-sites.geojson",
        "endpoint": f"{_ARCGIS}/Boating_Access_Sites_OA/FeatureServer/0",
        "out_fields": ["name", "waterway_name", "facility_type", "launch_type"],
    },
)


def _engine_default() -> MapConfig:
    """Build the engine-default MapConfig from the WKCC/Oregon data above."""
    return MapConfig(
        center=list(_DEFAULT_CENTER),
        zoom=_DEFAULT_ZOOM,
        bbox=list(_DEFAULT_BBOX),
        layers=[MapLayer(**spec) for spec in _DEFAULT_LAYERS],
    )


def load_map_config(dataset_dir: Path) -> MapConfig:
    """Resolve the map config for *dataset_dir*.

    Absent ``map.yaml`` → engine defaults. A present file fully defines the config
    (parsed strict-safe, validated fail-closed); an unreadable/malformed file, a
    non-mapping top level, an unknown key, a non-string mapping key, or a field that
    fails validation raises ``ValueError``. Mirrors
    :func:`kayak.dataset.region.load_region_config`.
    """
    path = dataset_dir / MAP_YAML
    if not path.is_file():
        return _engine_default()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{MAP_YAML}: unreadable ({e})") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(f"{MAP_YAML}: invalid YAML ({e})") from e
    if data is None:
        return MapConfig()  # an empty file = default extent, no overlay layers
    if not isinstance(data, dict):
        raise ValueError(f"{MAP_YAML}: top-level value must be a mapping")
    bad_keys = [k for k in data if not isinstance(k, str)]
    if bad_keys:
        raise ValueError(f"{MAP_YAML}: non-string key(s): {sorted(bad_keys, key=str)}")
    try:
        return MapConfig(**data)
    except ValueError as e:
        raise ValueError(f"{MAP_YAML}: {e}") from e


@lru_cache(maxsize=1)
def get_map_config() -> MapConfig:
    """Cached map config resolved from the configured ``DATASET_DIR``.

    Mirrors :func:`kayak.dataset.region.get_region_config`; tests that point
    ``DATASET_DIR`` at a fixture must call ``get_map_config.cache_clear()``.
    """
    from kayak.config import DATASET_DIR

    return load_map_config(DATASET_DIR)
