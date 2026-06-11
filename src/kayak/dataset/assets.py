"""Dataset-owned public site assets (S3 assets).

Datasets may override a narrow set of packaged static fallbacks by placing files
under ``site/assets/``. The allowlist is intentionally small because these files
are copied verbatim into the public docroot and referenced by HTML/meta tags.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

SITE_ASSETS_DIR = Path("site") / "assets"
README = "README.md"


@dataclass(frozen=True)
class SiteAssetSpec:
    filename: str
    kind: str
    size: tuple[int, int]
    max_bytes: int


SITE_ASSETS: tuple[SiteAssetSpec, ...] = (
    SiteAssetSpec("favicon.ico", "ico", (32, 32), 128 * 1024),
    SiteAssetSpec("icon-180.png", "png", (180, 180), 128 * 1024),
    SiteAssetSpec("icon-192.png", "png", (192, 192), 128 * 1024),
    SiteAssetSpec("og-image.png", "png", (1200, 630), 512 * 1024),
)
_SITE_ASSET_SPECS = {spec.filename: spec for spec in SITE_ASSETS}


def validate_site_assets(dataset_dir: Path) -> list[str]:
    """Validate optional ``site/assets/`` files and return focused errors."""
    asset_dir = dataset_dir / SITE_ASSETS_DIR
    if not asset_dir.exists():
        return []
    if asset_dir.is_symlink():
        return [f"{SITE_ASSETS_DIR}: symlinks are not supported"]
    if not asset_dir.is_dir():
        return [f"{SITE_ASSETS_DIR}: must be a directory"]

    errors: list[str] = []
    for child in sorted(asset_dir.iterdir()):
        errors.extend(_validate_site_asset_entry(child))
    return errors


def dataset_asset_overrides(dataset_dir: Path) -> dict[str, Path]:
    """Return valid dataset asset override paths keyed by public static filename.

    Raises ``ValueError`` when ``site/assets/`` is present but invalid. Build uses
    this as a second line of defense so a skipped deploy validation cannot publish
    malformed public assets.
    """
    errors = validate_site_assets(dataset_dir)
    if errors:
        raise ValueError("; ".join(errors))
    asset_dir = dataset_dir / SITE_ASSETS_DIR
    if not asset_dir.is_dir():
        return {}
    return {
        spec.filename: asset_dir / spec.filename
        for spec in SITE_ASSETS
        if (asset_dir / spec.filename).is_file()
    }


def _validate_readme(path: Path, rel: Path) -> list[str]:
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}: unreadable UTF-8 ({exc})"]
    return []


def _validate_site_asset_entry(path: Path) -> list[str]:
    rel = SITE_ASSETS_DIR / path.name
    if path.is_symlink():
        return [f"{rel}: symlinks are not supported"]
    if path.is_dir():
        return [f"{rel}: subdirectories are not supported"]
    if not path.is_file():
        return [f"{rel}: must be a regular file"]
    if path.name == README:
        return _validate_readme(path, rel)
    spec = _SITE_ASSET_SPECS.get(path.name)
    if spec is None:
        return [f"{rel}: unexpected site asset"]
    return _validate_asset(path, rel, spec)


def _validate_asset(path: Path, rel: Path, spec: SiteAssetSpec) -> list[str]:
    errors: list[str] = []
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [f"{rel}: unreadable ({exc})"]
    if len(data) > spec.max_bytes:
        errors.append(f"{rel}: file too large ({len(data)} bytes > {spec.max_bytes})")
        return errors
    try:
        size = _png_size(data) if spec.kind == "png" else _ico_size(data)
    except ValueError as exc:
        errors.append(f"{rel}: {exc}")
        return errors
    if size != spec.size:
        errors.append(f"{rel}: expected {spec.size[0]}x{spec.size[1]}, got {size[0]}x{size[1]}")
    return errors


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("must be a PNG image")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _ico_size(data: bytes) -> tuple[int, int]:
    if len(data) < 22:
        raise ValueError("must be an ICO image")
    reserved, image_type, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or image_type != 1 or count < 1:
        raise ValueError("must be an ICO image")
    width = data[6] or 256
    height = data[7] or 256
    return int(width), int(height)
