"""Typed host configuration — deployment-owned settings (S7/S8, Batch 4).

The dataset-separation boundary table puts *host* concerns — paths, service
users, domains/certificates, log locations, backup destinations/retention,
schedules — in deployment configuration, not in the engine or the dataset.
This module is the typed schema of record for those settings.

**Resolution order**: engine defaults < ``/etc/kayak/host.yaml`` (or the file
named by ``$KAYAK_HOST_CONFIG``). The file is optional: a missing file means
"use the defaults", which during the Batch 4 rollout are the **current WKCC
values** so every consumer is behavior-neutral until the live host gets an
explicit ``host.yaml`` (the same keep-current-defaults-then-flip pattern the
S3 site/region/map slices used). The cutover step (Batch 4C) ships prod's
``host.yaml`` and then flips these defaults to generic.

Consumers (growing through Batch 4): ``levels status`` (timezone, log glob,
output path, docroot, certificate host, backup locations/labels — S7
"parameterize status checks") and the backup jobs' rendered environment
(S8). The systemd-unit/nginx-vhost renderers land with the paired-release
installer (4B/4C).

Secrets never live here — ``/etc/kayak`` secret files and systemd
credentials stay separate (S8); this file is non-secret host shape only.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

HOST_YAML = "/etc/kayak/host.yaml"

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# rclone remote names: word characters and hyphens (no ':' — the colon is
# syntax, appended by consumers).
_RCLONE_REMOTE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# POSIX-portable service account name (useradd's NAME_REGEX): start with a
# lower-case letter or underscore, then lower/digit/underscore/hyphen.
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")
# PHP-FPM pool version as it appears in the /etc/php/<v>/fpm path (major.minor).
_PHP_VERSION_RE = re.compile(r"^\d+\.\d+$")


class HostConfig(BaseModel):
    """Non-secret deployment shape for one host.

    ``extra="forbid"`` so a typo'd ``host.yaml`` key is a hard error rather
    than a silently ignored setting, matching SiteConfig/RegionConfig.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- status page / operator checks (S7) ---
    timezone: str = "America/Los_Angeles"
    nginx_log_glob: str = "/var/log/nginx/levels-*.access.log*"
    status_output: str = "/home/pat/var/status.html"
    docroot: str = "/home/pat/public_html"
    cert_host: str = "levels.wkcc.org"

    # --- paired-release cutover / renderers (4C) ---
    # The service account + its home, the paired-release root, and the PHP-FPM
    # pool version the systemd-unit / nginx-vhost / FPM renderers need. Defaults
    # are the current WKCC values (keep-current-then-flip): a host with no
    # host.yaml renders the live shape, and the cutover host.yaml flips them.
    # ``docroot`` (above) is reused — it stays ``public_html`` until the cutover
    # host.yaml sets it to ``/var/cache/kayak/docroot`` (= the deployer's
    # ``KAYAK_DOCROOT``). Paths the renderers derive, NOT stored: the release venv
    # (``{release_root}/current/venv``), the release dataset
    # (``{release_root}/current/dataset``), and the FPM pool
    # (``/etc/php/{fpm_pool_php}/fpm/pool.d/kayak.conf``). See
    # docs/PLAN_4c_renderers.md.
    service_user: str = "pat"
    service_home: str = "/home/pat"
    release_root: str = "/opt/kayak"
    fpm_pool_php: str = "8.4"
    # Generated-runtime-data dirs that the engine defaults resolve RELATIVE to the
    # install root (config.py BASE_DIR): fetch-osmb's map-layer staging and the
    # gauge-audit metadata cache. In the editable install they land under the repo
    # checkout; under an immutable /opt/kayak/current release that root is
    # read-only, so the cutover unit drop-ins must point these at stable writable
    # cache paths. Keep-current defaults (the live repo-relative locations); the
    # cutover host.yaml flips them to /var/cache/kayak/* (regenerable cache,
    # alongside the #3 docroot). map_layers_dir is a dir; gauge_metadata_cache is
    # the sqlite FILE.
    map_layers_dir: str = "/home/pat/kayak/var/osmb"
    gauge_metadata_cache: str = "/home/pat/kayak/Gauge-metadata-cache/gauges.db"

    # --- backup policy (S8) ---
    backup_dir: str = "/home/pat/backups"
    offsite_remote: str = "gdrive-crypt"
    offsite_keep: int = 26

    @property
    def offsite_label(self) -> str:
        """Display label for the offsite destination (status page)."""
        return f"rclone → {self.offsite_remote}:"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"must be an IANA timezone name (got {v!r})") from e
        return v

    @field_validator(
        "nginx_log_glob",
        "status_output",
        "docroot",
        "backup_dir",
        "service_home",
        "release_root",
        "map_layers_dir",
        "gauge_metadata_cache",
    )
    @classmethod
    def _abs_path(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"must be an absolute path (got {v!r})")
        return v

    @field_validator("service_user")
    @classmethod
    def _username(cls, v: str) -> str:
        # POSIX-portable service account name: lower/digit/underscore/hyphen,
        # not starting with a hyphen. Renderers interpolate it into unit User=
        # and shell ACL commands, so reject anything that isn't a bare name.
        if not _USERNAME_RE.match(v):
            raise ValueError(f"must be a bare POSIX username (got {v!r})")
        return v

    @field_validator("fpm_pool_php")
    @classmethod
    def _php_version(cls, v: str) -> str:
        # PHP-FPM pool version as it appears in /etc/php/<v>/fpm — major.minor
        # only (Debian packages php8.4, never a patch level in the path).
        if not _PHP_VERSION_RE.match(v):
            raise ValueError(f"must be a major.minor PHP version like '8.4' (got {v!r})")
        return v

    @field_validator("cert_host")
    @classmethod
    def _hostname(cls, v: str) -> str:
        if not _HOSTNAME_RE.match(v):
            raise ValueError(f"must be a bare DNS hostname (got {v!r})")
        return v

    @field_validator("offsite_remote")
    @classmethod
    def _remote(cls, v: str) -> str:
        if not _RCLONE_REMOTE_RE.match(v):
            raise ValueError(f"must be a bare rclone remote name, no colon (got {v!r})")
        return v

    @field_validator("offsite_keep")
    @classmethod
    def _keep(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must keep at least 1 offsite backup")
        return v


def load_host_config(path: Path | None = None) -> HostConfig:
    """Load ``host.yaml`` from *path*, ``$KAYAK_HOST_CONFIG``, or the default
    location. A missing file yields the engine defaults; a malformed one is a
    hard ``ValueError`` (fail-closed — a half-read host config must not let a
    consumer silently fall back to another host's defaults).
    """
    if path is None:
        path = Path(os.environ.get("KAYAK_HOST_CONFIG", HOST_YAML))
    if not path.is_file():
        return HostConfig()
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: malformed YAML: {e}") from e
    if data is None:
        return HostConfig()
    if not isinstance(data, dict):
        raise ValueError(f"{path}: must be a mapping")
    bad_keys = [k for k in data if not isinstance(k, str)]
    if bad_keys:
        raise ValueError(f"{path}: non-string key(s): {bad_keys!r}")
    try:
        return HostConfig(**data)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e


@lru_cache(maxsize=1)
def get_host_config() -> HostConfig:
    """Process-cached accessor (mirrors get_site_config / get_region_config)."""
    return load_host_config()
