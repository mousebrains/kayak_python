"""S3 presentation-boundary guards.

The engine may ship generic fallback presentation, but WKCC/domain/regional
content must arrive from the dataset. This test intentionally scans only public
fallback text surfaces; deployment/status/operator files remain S7/S8 scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.resources import resource_dir

_STATIC_TEXT_FILES = (
    "feature-map.js",
    "filters.js",
    "gauge_picker.js",
    "gradient-profile.js",
    "internal-sort.js",
    "levels.js",
    "manifest.json",
    "map.js",
    "picker.js",
    "plot-hover.js",
    "scroll-indicator.js",
    "search-map.js",
    "security.txt",
    "style.css",
    "sw.js",
)

_PHP_PRESENTATION_FILES = (
    "about.php",
    "contact.php",
    "disclaimer.php",
    "privacy.php",
    "includes/footer.php",
    "includes/header.php",
    "includes/mail.php",
)

_FORBIDDEN_PRESENTATION_STRINGS = (
    "/home/pat",
    "gdrive-crypt",
    "levels.mousebrains.com",
    "levels.wkcc.org",
    "mousebrains.com",
    "noreply@levels.wkcc.org",
    "Oregon State Marine",
    "Oregon SMB",
    "pat.kayak",
    "Pat Welch",
    "Soggy Sneakers",
    "status.mousebrains.com",
    "Willamette Kayak",
    "WKCC",
)


def _public_fallback_text_files() -> list[Path]:
    web = resource_dir("web")
    files: list[Path] = []
    files.extend(sorted((web / "install-templates").glob("*")))
    files.extend(sorted((web / "legal").glob("*.txt")))
    files.extend(web / "static" / name for name in _STATIC_TEXT_FILES)
    files.extend(web / "php" / name for name in _PHP_PRESENTATION_FILES)
    return files


@pytest.mark.parametrize("path", _public_fallback_text_files(), ids=lambda p: str(p.name))
def test_public_fallback_presentation_is_region_neutral(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for needle in _FORBIDDEN_PRESENTATION_STRINGS:
        assert needle not in text, f"{path} leaked {needle!r}"
