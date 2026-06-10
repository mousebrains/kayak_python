"""Dataset-owned region presentation (``region.yaml``) — S3b.

Per-state external-resource links + weather URLs rendered on the ``{state}.html``
landing pages and the nav "Weather" button. A club supplies its own via an opt-in
``region.yaml`` at the dataset root (``DATASET_DIR``); resolution is
*engine defaults < dataset ``region.yaml``* (a present file fully defines the
region config — replace, not per-state merge). This is the region analogue of
:mod:`kayak.dataset.site`.

**Build-side only** — PHP renders no per-state links, so (unlike ``site.yaml``)
this never flows through ``emit-config``. Engine defaults are generic and contain
no curated state links; production WKCC region data lives in the WKCC dataset's
``region.yaml``. Labels + URLs render into HTML, so they're validated to a safe
shape here (fail-closed), and ``levels validate-dataset`` runs the same validation
at the deploy gate.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from kayak.dataset.layout import is_safe_state_name

REGION_YAML = "region.yaml"

# Labels are HTML text → reject every metacharacter. URLs land in a
# double-quoted href, so reject the attribute/tag-breakout chars but allow the
# legitimate URL chars (``&`` query separators, ``#`` fragments).
_LABEL_META_RE = re.compile(r"""[<>"'&]""")
_URL_META_RE = re.compile(r"""[<>"]""")


def _safe_label(v: str) -> str:
    if not v or not v.strip():
        raise ValueError("label must be a non-empty string")
    if _LABEL_META_RE.search(v):
        raise ValueError("label must not contain HTML metacharacters (< > \" ' &)")
    return v


def _safe_http_url(v: str) -> str:
    if _URL_META_RE.search(v):
        raise ValueError('url must not contain HTML metacharacters (< > ")')
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"url must be an http(s) URL with a host (got {v!r})")
    return v


class RegionLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    url: str

    _v_label = field_validator("label")(classmethod(lambda cls, v: _safe_label(v)))
    _v_url = field_validator("url")(classmethod(lambda cls, v: _safe_http_url(v)))


class StateRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    weather_url: str | None = None
    links: list[RegionLink] = []

    @field_validator("weather_url")
    @classmethod
    def _v_weather(cls, v: str | None) -> str | None:
        return None if v is None else _safe_http_url(v)


class RegionConfig(BaseModel):
    """Typed region presentation. Engine defaults are generic and have no state links."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_weather_url: str = "https://www.windy.com/?0.0,0.0,2"
    states: dict[str, StateRegion] = {}

    @field_validator("default_weather_url")
    @classmethod
    def _v_default_weather(cls, v: str) -> str:
        return _safe_http_url(v)

    @field_validator("states")
    @classmethod
    def _v_state_names(cls, v: dict[str, StateRegion]) -> dict[str, StateRegion]:
        # State keys become URL path segments, on-disk filenames, and HTML text in
        # the build, so reject anything that could path-traverse or inject markup.
        for name in v:
            if not is_safe_state_name(name):
                raise ValueError(
                    f"state name {name!r} is not a safe name (ASCII letter words only)"
                )
        return v

    def weather_url_for(self, state: str) -> str:
        """The state's weather URL, falling back to ``default_weather_url``."""
        s = self.states.get(state)
        return s.weather_url if s and s.weather_url else self.default_weather_url

    def has_state_weather(self, state: str) -> bool:
        """Whether *state* has its own weather URL (drives the nav label)."""
        s = self.states.get(state)
        return bool(s and s.weather_url)

    def links_for(self, state: str) -> list[tuple[str, str]]:
        """``[(label, url), …]`` for *state* (empty when the state has no entry)."""
        s = self.states.get(state)
        return [(link.label, link.url) for link in s.links] if s else []


# --------------------------------------------------------------------------- #
# Engine defaults — a generic empty region config. WKCC per-state links and
# weather URLs are dataset-owned in kayak_data/region.yaml as of S3b-D2.
# --------------------------------------------------------------------------- #

_DEFAULT_WEATHER_URL = "https://www.windy.com/?0.0,0.0,2"


def _engine_default() -> RegionConfig:
    """Build the generic engine-default RegionConfig."""
    return RegionConfig(default_weather_url=_DEFAULT_WEATHER_URL, states={})


def load_region_config(dataset_dir: Path) -> RegionConfig:
    """Resolve the region presentation for *dataset_dir*.

    Absent ``region.yaml`` → engine defaults. A present file fully defines the
    config (parsed strict-safe, validated fail-closed); an unreadable/malformed
    file, a non-mapping top level, an unknown key, a non-string mapping key, or a
    field that fails validation raises ``ValueError``. Mirrors
    :func:`kayak.dataset.site.load_site_config`.
    """
    path = dataset_dir / REGION_YAML
    if not path.is_file():
        return _engine_default()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{REGION_YAML}: unreadable ({e})") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(f"{REGION_YAML}: invalid YAML ({e})") from e
    if data is None:
        return RegionConfig()  # an empty file = no states, just defaults
    if not isinstance(data, dict):
        raise ValueError(f"{REGION_YAML}: top-level value must be a mapping")
    bad_keys = [k for k in data if not isinstance(k, str)]
    if bad_keys:
        raise ValueError(f"{REGION_YAML}: non-string key(s): {sorted(bad_keys, key=str)}")
    try:
        return RegionConfig(**data)
    except ValueError as e:
        raise ValueError(f"{REGION_YAML}: {e}") from e


@lru_cache(maxsize=1)
def get_region_config() -> RegionConfig:
    """Cached region config resolved from the configured ``DATASET_DIR``.

    Mirrors :func:`kayak.dataset.site.get_site_config`; tests that point
    ``DATASET_DIR`` at a fixture must call ``get_region_config.cache_clear()``.
    """
    from kayak.config import DATASET_DIR

    return load_region_config(DATASET_DIR)
