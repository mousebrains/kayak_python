"""Dataset-owned region presentation (``region.yaml``) — S3b.

Per-state external-resource links + weather URLs rendered on the ``{state}.html``
landing pages and the nav "Weather" button. A club supplies its own via an opt-in
``region.yaml`` at the dataset root (``DATASET_DIR``); resolution is
*engine defaults < dataset ``region.yaml``* (a present file fully defines the
region config — replace, not per-state merge). This is the region analogue of
:mod:`kayak.dataset.site`.

**Build-side only** — PHP renders no per-state links, so (unlike ``site.yaml``)
this never flows through ``emit-config``. Engine defaults are the current WKCC
region data, so a dataset without ``region.yaml`` renders identically through S3;
the generic-default flip is deferred to S3i. Labels + URLs render into HTML, so
they're validated to a safe shape here (fail-closed), and ``levels
validate-dataset`` runs the same validation at the deploy gate.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

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
    """Typed region presentation. Engine defaults are the current WKCC data (S3b)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_weather_url: str = "https://www.windy.com/?43.0,-118.0,6"
    states: dict[str, StateRegion] = {}

    @field_validator("default_weather_url")
    @classmethod
    def _v_default_weather(cls, v: str) -> str:
        return _safe_http_url(v)

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
# Engine defaults — the current WKCC per-state region data. Moved here verbatim
# from web/build/shell.py (S3b-1); a dataset region.yaml overrides it wholesale.
# Genericized (emptied) in S3i once the WKCC dataset carries its own region.yaml.
# --------------------------------------------------------------------------- #

_DEFAULT_WEATHER_URL = "https://www.windy.com/?43.0,-118.0,6"

_DEFAULT_STATE_WEATHER_URL: dict[str, str] = {
    "Oregon": "https://www.windy.com/?44.0,-120.5,7",
    "Washington": "https://www.windy.com/?47.5,-120.5,7",
    "Idaho": "https://www.windy.com/?44.4,-114.7,7",
    "Nevada": "https://www.windy.com/?39.5,-116.9,7",
    "California": "https://www.windy.com/?37.2,-119.5,6",
    "Montana": "https://www.windy.com/?46.9,-110.4,6",
}

_DEFAULT_STATE_LINKS: dict[str, list[tuple[str, str]]] = {
    "Oregon": [
        (
            "American Whitewater — Oregon",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-ORE",
        ),
        (
            "Dreamflows — Oregon Coastal",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Coastal_Rivers",
        ),
        (
            "Dreamflows — Oregon Central",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Central_Rivers",
        ),
        (
            "Dreamflows — Oregon Eastern",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Eastern_Rivers",
        ),
        ("Oregon Kayaking", "https://oregonkayaking.net"),
        ("USGS Oregon Water Data", "https://waterdata.usgs.gov/state/oregon/"),
        ("USGS StreamStats", "https://streamstats.usgs.gov/ss/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Willamette Kayak and Canoe Club", "https://wkcc.org"),
        ("Oregon Whitewater Association", "https://oregonwhitewater.org"),
        ("Oregon Weather — Windy", "https://www.windy.com/?44.0,-120.5,7"),
        ("Oregon State Marine Board", "https://www.oregon.gov/osmb/pages/index.aspx"),
        (
            "Oregon Waterway Access Permits",
            "https://www.oregon.gov/osmb/boater-info/pages/ais-faqs.aspx",
        ),
        (
            "Report a boating obstruction (Oregon SMB)",
            "https://oregon-boating-obstructions-geo.hub.arcgis.com",
        ),
    ],
    "Washington": [
        (
            "American Whitewater — Washington",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-WSH",
        ),
        (
            "Dreamflows — Washington",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Washington_Rivers",
        ),
        ("USGS Washington Water Data", "https://waterdata.usgs.gov/state/washington/"),
        ("USGS StreamStats", "https://streamstats.usgs.gov/ss/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Professor Paddle", "https://www.professorpaddle.com"),
        ("Washington Weather — Windy", "https://www.windy.com/?47.5,-120.5,7"),
        ("Washington Kayak Club", "http://wakayakclub.clubexpress.com"),
    ],
    "Idaho": [
        (
            "American Whitewater — Idaho",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-IDA",
        ),
        (
            "Dreamflows — Idaho",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Idaho_Rivers",
        ),
        ("USGS Idaho Water Data", "https://waterdata.usgs.gov/state/idaho/"),
        ("USGS StreamStats", "https://streamstats.usgs.gov/ss/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Idaho Rivers United", "https://www.idahorivers.org"),
        ("Idaho Whitewater Association", "https://idahowhitewater.org"),
        ("Idaho Dept. of Water Resources", "https://idwr.idaho.gov"),
        ("Idaho Weather — Windy", "https://www.windy.com/?44.4,-114.7,7"),
    ],
    "Nevada": [
        ("USGS Nevada Water Data", "https://waterdata.usgs.gov/state/nevada/"),
        ("USGS StreamStats", "https://streamstats.usgs.gov/ss/"),
        ("Colorado Basin River Forecast Center", "https://www.cbrfc.noaa.gov"),
        (
            "American Whitewater — Nevada",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-NEV",
        ),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Nevada Weather — Windy", "https://www.windy.com/?39.5,-116.9,7"),
    ],
    "California": [
        ("Dreamflows", "https://www.dreamflows.com"),
        (
            "American Whitewater — California",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-CAL",
        ),
        ("USGS California Water Data", "https://waterdata.usgs.gov/state/california/"),
        ("USGS StreamStats", "https://streamstats.usgs.gov/ss/"),
        ("California Nevada River Forecast Center", "https://www.cnrfc.noaa.gov"),
        ("California Creeks", "https://cacreeks.com"),
        ("Gold Country Paddlers", "https://goldcountrypaddlers.org"),
        ("California Weather — Windy", "https://www.windy.com/?37.2,-119.5,6"),
    ],
    "Montana": [
        (
            "American Whitewater — Montana",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-MNT",
        ),
        ("USGS Montana Water Data", "https://waterdata.usgs.gov/state/montana/"),
        ("USGS StreamStats", "https://streamstats.usgs.gov/ss/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("Missouri Basin River Forecast Center", "https://www.weather.gov/mbrfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/gp/hydromet/"),
        ("Montana Weather — Windy", "https://www.windy.com/?46.9,-110.4,6"),
    ],
}


def _engine_default() -> RegionConfig:
    """Build the engine-default RegionConfig from the WKCC data above."""
    states = {
        name: StateRegion(
            weather_url=_DEFAULT_STATE_WEATHER_URL.get(name),
            links=[RegionLink(label=label, url=url) for label, url in links],
        )
        for name, links in _DEFAULT_STATE_LINKS.items()
    }
    return RegionConfig(default_weather_url=_DEFAULT_WEATHER_URL, states=states)


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
