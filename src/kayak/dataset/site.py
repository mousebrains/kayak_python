"""Dataset-owned site identity (``site.yaml``) — S3a.

A club running the engine supplies its own branding/identity through a
``site.yaml`` at the dataset root (``DATASET_DIR``), alongside the
:mod:`kayak.dataset.contract` ``dataset.yaml``. This is the presentation
analogue of the metadata CSVs: typed, validated, dataset-owned content.

**Resolution order** is *engine defaults < dataset ``site.yaml``*. The keys here
have no host-env override today (the env-backed identity keys — ``SITE_URL``,
``MAINTAINER_NAME``, ``MAIL_*`` — stay in :class:`kayak.config.KayakConfig`,
which already layers env over its defaults). ``site.yaml`` is **opt-in**: a
dataset without one renders with generic engine defaults. Production WKCC
identity lives in the WKCC dataset's explicit ``site.yaml``.

The values flow to two consumers: the static build (``kayak.web.build`` reads
:func:`get_site_config`) and PHP (``levels emit-config`` embeds the resolved
block in the runtime-config JSON). Because both render the values into HTML, the
free-text/URL/color fields are validated to a safe shape here (fail-closed), and
``levels validate-dataset`` runs the same validation at the deploy gate.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

SITE_YAML = "site.yaml"

# Hex color (#rrggbb) and a no-HTML-metacharacter guard for free-text fields that
# land in HTML attributes/text (og:site_name, footer label) — reject the chars
# that could break out of an attribute or inject a tag. Mirrors the fail-closed
# spirit of the S2 regression sanitizer.
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_HTML_META_RE = re.compile(r"""[<>"'&]""")
_LINE_BREAK_RE = re.compile(r"[\r\n]")
_WHITESPACE_RE = re.compile(r"\s")
_SECURITY_EXPIRES_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_ORG_LABEL_STOPWORDS = {"and", "of", "the", "for"}


def _safe_text_value(v: str) -> str:
    if not v or not v.strip():
        raise ValueError("must be a non-empty string")
    if _HTML_META_RE.search(v):
        raise ValueError("must not contain HTML metacharacters (< > \" ' &)")
    return v


def _short_org_label(name: str) -> str:
    """Derive a compact nav label from an organization name.

    ``Willamette Kayak and Canoe Club`` becomes ``WKCC``; short single-word
    names remain unchanged. The input has already passed the safe-text validator.
    """
    words = _WORD_RE.findall(name)
    initials = "".join(
        word[0].upper() for word in words if word.lower() not in _ORG_LABEL_STOPWORDS
    )
    return initials if 2 <= len(initials) <= 6 else name


class SiteConfig(BaseModel):
    """Typed site identity. Engine defaults are generic; datasets own branding.

    ``extra="forbid"`` so an unknown ``site.yaml`` key is a hard error (caught by
    ``validate-dataset``), matching the dataset contract's reject-unknown rule.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    site_name: str = "River Levels"
    org_name: str = "Kayak"
    org_url: str = "https://example.com"
    org_label: str = ""
    manifest_name: str = ""
    manifest_short_name: str = "Levels"
    # Empty means use the packaged security.txt line; datasets opt in per field.
    security_contact: str = ""
    security_expires: str = ""
    # Colors are strict 6-digit ``#rrggbb`` only — no 3-digit (#fff), ``rgb()``, or
    # named colors. Stricter than CSS on purpose (the safe direction: the value
    # lands in both an HTML attribute and a CSS property).
    brand_color: str = "#1b5591"
    brand_color_dark: str = "#0d3057"
    attribution: str = "dataset contributors"

    def model_post_init(self, __context: Any) -> None:
        """Fill derived display fields when the dataset omits them."""
        if self.org_label == "":
            object.__setattr__(self, "org_label", _short_org_label(self.org_name))
        if self.manifest_name == "":
            object.__setattr__(self, "manifest_name", self.site_name)

    @field_validator("site_name", "org_name", "attribution", "manifest_short_name")
    @classmethod
    def _safe_text(cls, v: str) -> str:
        return _safe_text_value(v)

    @field_validator("manifest_name")
    @classmethod
    def _safe_manifest_name(cls, v: str) -> str:
        return v if v == "" else _safe_text_value(v)

    @field_validator("security_contact")
    @classmethod
    def _security_contact(cls, v: str) -> str:
        if v == "":
            return v
        if not v.strip():
            raise ValueError("must be empty or a non-empty URI")
        if _LINE_BREAK_RE.search(v):
            raise ValueError("must not contain line breaks")
        if _WHITESPACE_RE.search(v):
            raise ValueError("must not contain whitespace")
        parsed = urlparse(v)
        if parsed.scheme == "mailto":
            if not parsed.path or "@" not in parsed.path:
                raise ValueError("mailto contact must include an email address")
            return v
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return v
        raise ValueError("must be a mailto:, http:, or https: URI")

    @field_validator("security_expires")
    @classmethod
    def _security_expires(cls, v: str) -> str:
        if v == "":
            return v
        if not v.strip():
            raise ValueError("must be empty or an RFC3339 UTC timestamp")
        if _LINE_BREAK_RE.search(v):
            raise ValueError("must not contain line breaks")
        if not _SECURITY_EXPIRES_RE.match(v):
            raise ValueError("must be an RFC3339 UTC timestamp like 2027-05-20T00:00:00Z")
        try:
            datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as e:
            raise ValueError("must be a valid RFC3339 UTC timestamp") from e
        return v

    @field_validator("org_label")
    @classmethod
    def _safe_org_label(cls, v: str) -> str:
        return _safe_text_value(v)

    @field_validator("brand_color", "brand_color_dark")
    @classmethod
    def _hex_color(cls, v: str) -> str:
        if not _HEX_COLOR_RE.match(v):
            raise ValueError(f"must be a #rrggbb hex color (got {v!r})")
        return v

    @field_validator("org_url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if _HTML_META_RE.search(v):
            raise ValueError("must not contain HTML metacharacters")
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"must be an http(s) URL with a host (got {v!r})")
        return v


def load_site_config(dataset_dir: Path) -> SiteConfig:
    """Resolve the site identity for *dataset_dir*.

    Absent ``site.yaml`` → all engine defaults. A present file is parsed (strict
    safe YAML) and overlaid on the defaults; an unreadable/malformed file, a
    non-mapping top level, an unknown key, or a field that fails validation raises
    ``ValueError`` (corruption is distinct from absence). Mirrors
    :func:`kayak.dataset.contract.load_dataset_meta`.
    """
    path = dataset_dir / SITE_YAML
    if not path.is_file():
        return SiteConfig()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{SITE_YAML}: unreadable ({e})") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(f"{SITE_YAML}: invalid YAML ({e})") from e
    if data is None:
        return SiteConfig()  # an empty file is "no overrides", not corruption
    if not isinstance(data, dict):
        raise ValueError(f"{SITE_YAML}: top-level value must be a mapping")
    # YAML allows non-string mapping keys (e.g. ``1: foo``). ``SiteConfig(**data)``
    # would raise a raw TypeError on those — which ``_check_site_yaml`` (catching
    # only ValueError) would let escape as a validator crash. Reject them here as a
    # reported error instead, naming the bad key (parallels contract.py).
    bad_keys = [k for k in data if not isinstance(k, str)]
    if bad_keys:
        raise ValueError(f"{SITE_YAML}: non-string key(s): {sorted(bad_keys, key=str)}")
    try:
        return SiteConfig(**data)
    except ValueError as e:
        # Pydantic ValidationError is a ValueError subclass; normalize the message.
        raise ValueError(f"{SITE_YAML}: {e}") from e


@lru_cache(maxsize=1)
def get_site_config() -> SiteConfig:
    """Cached site identity resolved from the configured ``DATASET_DIR``.

    Mirrors :func:`kayak.config.get_config`: a singleton for the running process.
    Tests that point ``DATASET_DIR`` at a fixture (or write a scratch
    ``site.yaml``) must call ``get_site_config.cache_clear()`` to pick it up.
    """
    from kayak.config import DATASET_DIR

    return load_site_config(DATASET_DIR)
