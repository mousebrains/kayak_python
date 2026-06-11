"""Shared constants and small helpers for the kayak.web.build package.

Top-level state evaluated at import time (file mtimes for cache-busting;
state-name dictionaries) lives in one place so every consumer sees the
same values.
"""

import logging
import os
import tempfile
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote as _urlquote

from kayak.config import SITE_URL
from kayak.dataset.license import get_data_license
from kayak.dataset.site import SiteConfig, get_site_config

logger = logging.getLogger(__name__)

# Resolved dataset site identity (S3a). Engine defaults are generic; the WKCC
# deployment gets its identity from kayak_data/site.yaml.
_SITE = get_site_config()

# Data freshness
DATA_STALE_THRESHOLD = timedelta(hours=48)
DATA_EXPIRY_THRESHOLD = timedelta(days=7)

# Branding (dataset-driven via site.yaml; defaults to the generic engine palette)
BRAND_COLOR = _SITE.brand_color
BRAND_COLOR_DARK = _SITE.brand_color_dark
# The brand color baked into the shipped static assets (style.css :root, manifest)
# — the engine default. _apply_brand_color swaps it for the resolved BRAND_COLOR so
# a dataset site.yaml rebrands the built CSS + PWA manifest (S3a-3).
_DEFAULT_BRAND_COLOR = SiteConfig().brand_color


def _state_page_path(state: str) -> str:
    """Site-relative URL path for a state landing page, percent-encoded.

    The on-disk file is ``<state>.html`` (raw name, e.g. ``New Mexico.html``); the
    served URL must encode the space (``/New%20Mexico.html``), which the server
    decodes back to the file. One-word states are unchanged (encode is a no-op), so
    prod output is byte-identical. Centralized so nav, og:url/canonical, and the
    sitemap all build the same encoded path (S3b-2 review).
    """
    return f"/{_urlquote(state)}.html"


def _apply_brand_color(text: str) -> str:
    """Swap the engine-default brand color for the dataset-resolved one.

    The shipped ``style.css`` (``:root`` ``--c-primary``/``--c-link``) and
    ``manifest.json`` (``theme_color``) carry the engine-default ``#rrggbb``; this
    substitutes the resolved :data:`BRAND_COLOR`. The default hex appears only at
    those brand declarations (the dark-mode ``--c-link`` is a different color), so
    a plain replace is safe. A no-op — byte-identical output — when the dataset
    uses the default brand (case-insensitively, so ``#1B5591`` doesn't churn the
    CSS hash for a visually identical color).
    """
    if BRAND_COLOR.lower() == _DEFAULT_BRAND_COLOR.lower():
        return text
    return text.replace(_DEFAULT_BRAND_COLOR, BRAND_COLOR)


def _data_license_label() -> str:
    """Dataset-owned public data-license label for footers."""
    return get_data_license().label


def _license_meta() -> dict[str, str]:
    """Machine-readable data-license metadata for downloadable JSON outputs."""
    return get_data_license().as_json_meta(attribution=_SITE.attribution)


_STATE_ABBREVS = {
    "Arizona": "AZ",
    "California": "CA",
    "Colorado": "CO",
    "Idaho": "ID",
    "Kansas": "KS",
    "Montana": "MT",
    "Nevada": "NV",
    "New Mexico": "NM",
    "Oregon": "OR",
    "Utah": "UT",
    "Washington": "WA",
    "Wyoming": "WY",
}

_ABBR_TO_STATE = {v: k for k, v in _STATE_ABBREVS.items()}


# CSS is read once from the source tree and inlined into every page.
# Path arithmetic: __file__ is .../src/kayak/web/build/_shared.py, so
# .parent.parent reaches .../src/kayak/web — append "static" to land at
# the kayak.web.static asset dir.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_CSS_PATH = _STATIC_DIR / "style.css"
_JS_PATH = _STATIC_DIR / "levels.js"
_FILTERS_JS_PATH = _STATIC_DIR / "filters.js"

# map.js ships in the packaged web/static dir alongside levels.js/filters.js
# (relocated there in dataset-separation S4a-2 slice B1), so it resolves in
# both an editable and a wheel install — no repo-root BASE_DIR lookup.
_MAP_JS_PATH = _STATIC_DIR / "map.js"

_LEVELS_JS_VERSION = int(_JS_PATH.stat().st_mtime)
_FILTERS_JS_VERSION = int(_FILTERS_JS_PATH.stat().st_mtime)
_MAP_JS_VERSION = int(_MAP_JS_PATH.stat().st_mtime)
_LEVELS_JS = f'<script src="/static/levels.js?v={_LEVELS_JS_VERSION}" defer></script>'


def _og_meta(title: str, desc: str, path: str = "") -> str:
    """OpenGraph + Twitter card meta block. `path` is site-relative ("/Oregon.html"); empty omits og:url + canonical."""
    site = SITE_URL.rstrip("/")
    image = f"{site}/static/og-image.png"
    canonical = f'<link rel="canonical" href="{site}{path}">\n' if path else ""
    og_url = f'<meta property="og:url" content="{site}{path}">\n' if path else ""
    return (
        f"{canonical}"
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="{_SITE.site_name}">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        f"{og_url}"
        f'<meta property="og:image" content="{image}">\n'
        f'<meta property="og:image:width" content="1200">\n'
        f'<meta property="og:image:height" content="630">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{title}">\n'
        f'<meta name="twitter:description" content="{desc}">'
    )


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write *content* to *path* atomically via temp file + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content)
        os.close(fd)
        fd = -1
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            os.unlink(tmp)
        raise


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via temp file + rename."""
    _atomic_write_bytes(path, content.encode())


def _load_css() -> str:
    try:
        css = _CSS_PATH.read_text()
    except FileNotFoundError:
        logger.warning("style.css not found at %s", _CSS_PATH)
        return ""
    # Apply the dataset brand color before the caller hashes the CSS, so a custom
    # brand yields its own content-addressed style-<hash>.css (S3a-3).
    return _apply_brand_color(css)


def _css_link_tag(css_hash: str) -> str:
    """Return the <link> tag that replaces per-page inline CSS."""
    return f'<link rel="stylesheet" href="/static/style-{css_hash}.css">'


def _editor_feature_on() -> bool:
    v = os.environ.get("EDITOR_FEATURE", "").strip().lower()
    return v in ("1", "true", "yes")
