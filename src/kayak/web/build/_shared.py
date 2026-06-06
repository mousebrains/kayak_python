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

from kayak.config import SITE_URL

logger = logging.getLogger(__name__)

# Data freshness
DATA_STALE_THRESHOLD = timedelta(hours=48)
DATA_EXPIRY_THRESHOLD = timedelta(days=7)

# Branding
BRAND_COLOR = "#1b5591"
BRAND_COLOR_DARK = "#0d3057"

# Embedded license attribution for machine-readable outputs. Added at the
# top level of every generated JSON file so the license travels with any
# downloaded copy. See LICENSE-DATA at the repo root for the full terms.
_LICENSE_META = {
    "license": "CC BY-NC 4.0",
    "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
    "attribution": "levels.wkcc.org",
    "notice": (
        "Metadata + calculated values: CC BY-NC 4.0. Observations: public domain at source."
    ),
}

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

# States shown in the nav bar (Oregon + adjacent states)
_NAV_STATES = {"Oregon", "Washington", "Idaho", "Nevada", "California", "Montana"}


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
        f'<meta property="og:site_name" content="WKCC River Levels">\n'
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
        return _CSS_PATH.read_text()
    except FileNotFoundError:
        logger.warning("style.css not found at %s", _CSS_PATH)
        return ""


def _css_link_tag(css_hash: str) -> str:
    """Return the <link> tag that replaces per-page inline CSS."""
    return f'<link rel="stylesheet" href="/static/style-{css_hash}.css">'


def _editor_feature_on() -> bool:
    v = os.environ.get("EDITOR_FEATURE", "").strip().lower()
    return v in ("1", "true", "yes")
