"""Application configuration.

The typed ``KayakConfig`` (pydantic-settings) is the source of truth.
Module-level constants below it are derived for source-compat with the
existing ``from kayak.config import DATABASE_URL`` call pattern; new
code should use ``get_config()`` (or instantiate ``KayakConfig()``
directly when test-monkeypatching env vars).

Phase 0 of `docs/done/PLAN_tier3_closeout.md` § T3.3: the schema lands; no
read-path consumers move yet. Phases 1-4 introduce ``levels
emit-config``, the PHP read path, and finally remove the module-level
constants.
"""

from __future__ import annotations

import os
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import AliasChoices, AnyHttpUrl, EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from kayak.resources import resource_dir


def _config_env_path() -> Path | None:
    """Locate ``~/.config/kayak/.env``, with a SUDO_USER fallback.

    When ``scripts/deploy.sh`` runs ``sudo -n levels emit-config``, the
    levels process is root but the operator (typically ``pat``) is named
    in ``SUDO_USER``. ``Path.home()`` resolves to ``/root`` in that case
    and the dotenv load is a no-op, so the snapshot would land with
    pydantic defaults instead of the operator's live env. Fall back to
    ``~SUDO_USER/.config/kayak/.env`` to keep emit-config's output
    consistent with what the operator sees from a normal shell.
    """
    primary = Path.home() / ".config" / "kayak" / ".env"
    if primary.exists():
        return primary
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd

            fallback = Path(pwd.getpwnam(sudo_user).pw_dir) / ".config" / "kayak" / ".env"
        except KeyError:
            return None
        if fallback.exists():
            return fallback
    return None


load_dotenv(_config_env_path())

# /etc/kayak/secrets.env (mode 0600 root:www-data) carries production
# secrets — TURNSTILE_SITE_KEY, TURNSTILE_SECRET — that are
# intentionally NOT in the operator's ~/.config/kayak/.env. pat can't
# read it, so the unprivileged `levels emit-config --dry-run` render
# (scripts/deploy.sh step 3.5, review-3 R1.5) never sees these values;
# the root-owned /usr/local/sbin/kayak-install-runtime-config wrapper
# merges them into the JSON before installing — without that merge,
# PHP's Config::str('turnstile_secret') returns empty and
# turnstile.php's `turnstile_enabled()` false-paths to
# `turnstile_verify() === true`, silently bypassing captcha (fired in
# prod; gpt-5.5 take-2 review 2026-06-03). This loader still applies
# for any privileged KayakConfig construction. `override=False` keeps
# the operator's .env (and the OS env, which tests use) winning over
# secrets.env — the wrapper's fill-if-absent merge mirrors the same
# precedence.
# Gate on os.access(): `load_dotenv` silently no-ops on a missing
# path but RAISES PermissionError when the file exists but isn't
# readable (dev shells where pat can't read root:www-data 0600
# files). Pre-checking with R_OK collapses both cases to a no-op.
_SECRETS_ENV = Path("/etc/kayak/secrets.env")
if _SECRETS_ENV.is_file() and os.access(_SECRETS_ENV, os.R_OK):
    load_dotenv(_SECRETS_ENV, override=False)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# Packaged engine resources (YAML defaults, schema migrations) ship inside the
# kayak package, so this resolves under src/kayak/ (editable) or
# site-packages/kayak/ (wheel) — not the repo root — which is what lets a
# wheel-installed engine find them. See kayak.resources / plan S4a-2.
DATA_DIR = resource_dir("data")


def _default_database_url() -> str:
    return f"sqlite:///{(BASE_DIR / '../DB/kayak.db').resolve()}"


def _default_output_dir() -> Path:
    return BASE_DIR / "public_html"


class KayakConfig(BaseSettings):
    """Typed env-driven configuration.

    Field names map case-insensitively to env vars (e.g. ``database_url``
    reads ``DATABASE_URL``). ``~/.config/kayak/.env`` is loaded into
    ``os.environ`` at module import time via ``load_dotenv`` above; the
    settings class itself does not re-read .env files.

    Healthchecks ``hc_*`` URLs are read by **systemd** (via
    ``EnvironmentFile=`` + ``${HC_*}`` shell expansion in
    ``ExecStartPost=``), not by Python or PHP. They live in the model
    so ``levels validate-config`` (Phase 1) can flag a missing one at
    deploy time and ``levels emit-config`` can write them into the JSON
    inventory.
    """

    # ``extra="forbid"`` catches typos in explicit ``KayakConfig(...)``
    # kwargs (test fixtures); it does NOT reject unrelated env vars,
    # because pydantic-settings only consults env vars whose names map
    # to declared fields. Typo-in-env-var-name protection lives in
    # ``levels validate-config --known-env`` (Phase 3.2).
    # ``validate_default=True`` runs field validators on the default
    # values too, so a default that drifts out of its constraint
    # (e.g. someone changes ``fetch_timeout``'s default to 0) fails
    # at instantiation, not at the first env override.
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="forbid",
        validate_default=True,
    )

    # Core
    database_url: str = Field(default_factory=_default_database_url)
    output_dir: Path = Field(default_factory=_default_output_dir)

    # Staging dir for build-EXTERNAL generated static inputs — today only the
    # OSMB hazard/access GeoJSON written by ``levels fetch-osmb``, which runs on
    # its own cadence and is then copied into ``OUTPUT_DIR/static`` by
    # ``levels build``. Like ``output_dir``/``dataset_dir`` it is a
    # ``BASE_DIR``-relative dev default overridden by env (``OSMB_DIR``) in real
    # deployments; it is deliberately NOT under the packaged ``DATA_DIR``/web
    # assets (it holds generated runtime data, not engine resources, and a wheel
    # install's package dir may be read-only).
    osmb_dir: Path = Field(default_factory=lambda: BASE_DIR / "var" / "osmb")

    # Dataset root — the directory holding the club-specific dataset (the
    # ``*.csv`` + ``reaches*.json`` the metadata-single-source flow treats as the
    # source of truth; S6 gives it a versioned contract). Read from ``DATASET_DIR``
    # (preferred) or the legacy ``METADATA_DIR`` (deprecated alias, honored for one
    # release; see ``_check_dataset_dir_env``). The default *value* stays
    # ``data/db`` (repo-root) and is deliberately NOT the packaged ``DATA_DIR``:
    # the dataset is club-specific external data, not an engine resource, so it
    # ships in a separate repo (clone ``kayak_data`` and set ``DATASET_DIR`` to it,
    # e.g. ``/home/pat/kayak_data``; deploy/SETUP.md § 2.5), not inside the wheel.
    # The schema migrations, by contrast, *are* an engine resource and ship inside
    # the package at ``src/kayak/data/db/migrations`` (resolved via ``DATA_DIR``),
    # so they are NOT under this root.
    dataset_dir: Path = Field(
        default_factory=lambda: BASE_DIR / "data" / "db",
        validation_alias=AliasChoices("DATASET_DIR", "METADATA_DIR"),
    )

    # Fetch pipeline
    fetch_timeout: int = Field(default=300, gt=0, le=600)
    fetch_budget: int = Field(default=240, gt=0, le=600)
    fetch_user_agent: str = "kayak/1.0"

    # Maintainer / site identity.
    # ``MAINTAINER_EMAIL`` env var is parsed comma-separated; empty list
    # is the contract for "no env override", which Phase 2+ PHP will
    # treat as "fall back to editor-status='maintainer' DB rows". The
    # singular ``MAINTAINER_EMAIL`` env var name is preserved by alias
    # so existing systemd / .env settings keep working unchanged.
    # ``NoDecode`` keeps pydantic-settings from JSON-parsing the env
    # value before our comma-split validator runs.
    maintainer_emails: Annotated[list[EmailStr], NoDecode] = Field(
        default_factory=list,
        validation_alias="MAINTAINER_EMAIL",
    )
    maintainer_name: str = "Pat Welch"
    site_url: AnyHttpUrl = Field(default=AnyHttpUrl("https://levels.wkcc.org"))

    # Notifications
    ntfy_topic: str | None = None

    # Mail
    mail_from: EmailStr | None = None
    mail_reply_to: EmailStr | None = None
    mail_dump_dir: Path | None = None

    # Operator email-digest destination. Read by scripts/audit_gauges.py
    # (the weekly gauge-metadata audit timer) and emitted into the JSON
    # so future PHP / Python consumers don't have to re-implement the env
    # read. Currently consumed only by the script, but living in the
    # typed model keeps it within validate-config's allowlist.
    audit_email: EmailStr | None = None

    # Where the CSP-violation reporter writes JSON lines. Hardcoded
    # operator-specific default for now; Phase 5 (T3.4) will swap to
    # ``${KAYAK_HOME}/logs/csp.log`` once that indirection lands.
    csp_log_path: Path = Path("/home/pat/logs/csp.log")

    # Editor surface
    editor_feature: bool = False
    editor_session_ttl_days: int = 7
    turnstile_site_key: str | None = None
    turnstile_secret: SecretStr | None = None

    # Healthchecks heartbeat URLs (consumed by systemd, not Python).
    hc_pipeline: AnyHttpUrl | None = None
    hc_backup_hourly: AnyHttpUrl | None = None
    hc_healthcheck: AnyHttpUrl | None = None
    hc_decimate: AnyHttpUrl | None = None
    hc_editor_retention: AnyHttpUrl | None = None
    hc_backup_weekly: AnyHttpUrl | None = None
    hc_backup_offsite: AnyHttpUrl | None = None
    hc_audit_gauges: AnyHttpUrl | None = None
    hc_heartbeat: AnyHttpUrl | None = None
    hc_cert_expiry: AnyHttpUrl | None = None
    hc_cert_renewal_test: AnyHttpUrl | None = None
    hc_config_drift: AnyHttpUrl | None = None
    hc_recap: AnyHttpUrl | None = None
    hc_fetch_osmb: AnyHttpUrl | None = None
    hc_status: AnyHttpUrl | None = None

    @field_validator("maintainer_emails", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


@lru_cache(maxsize=1)
def get_config() -> KayakConfig:
    """Cached config singleton mirroring today's import-time read.

    Tests that monkeypatch env vars should ``get_config.cache_clear()``
    or construct ``KayakConfig()`` directly to pick up the new env.
    """
    return KayakConfig()


def _check_dataset_dir_env() -> None:
    """Deprecation policy for the dataset-root env var (S6.1).

    Value resolution is handled by the ``dataset_dir`` field's ``AliasChoices``
    (``DATASET_DIR`` preferred, then the legacy ``METADATA_DIR``). This adds the
    policy on top: **warn** when only the legacy ``METADATA_DIR`` is set, and
    **fail fast** when both are set to *different* directories (a likely
    misconfiguration). It reads ``os.environ`` directly — case-insensitively, to
    match the model's ``case_sensitive=False`` — so it reflects the deployment
    env, not a test's direct-kwarg ``KayakConfig(...)`` construction. Exposed as
    a function so tests can drive it explicitly.
    """
    env = {k.upper(): v for k, v in os.environ.items()}
    ds = env.get("DATASET_DIR")
    md = env.get("METADATA_DIR")
    if ds is not None and md is not None and Path(ds).resolve() != Path(md).resolve():
        raise ValueError(
            "Both DATASET_DIR and METADATA_DIR are set to different paths "
            f"(DATASET_DIR={ds!r}, METADATA_DIR={md!r}). Set only DATASET_DIR."
        )
    if ds is None and md is not None:
        warnings.warn(
            "METADATA_DIR is deprecated; rename it to DATASET_DIR "
            "(METADATA_DIR is honored for one release, then removed).",
            DeprecationWarning,
            stacklevel=2,
        )


_config = get_config()
_check_dataset_dir_env()

# Module-level constants derived from the model — preserved for source-
# compat with existing ``from kayak.config import X`` callers. Phase 4
# of the typed-config plan removes these once all callers move to
# ``get_config()``.
DATABASE_URL: str = _config.database_url
OUTPUT_DIR: str = str(_config.output_dir)
# The dataset root (data/db by default; the kayak_data clone in real deployments).
DATASET_DIR: Path = _config.dataset_dir
# Deprecated alias of DATASET_DIR, kept for one release so existing
# ``from kayak.config import METADATA_DIR`` callers keep working.
METADATA_DIR: Path = _config.dataset_dir
# Staging dir for build-external generated static inputs (OSMB GeoJSON).
OSMB_DIR: Path = _config.osmb_dir
FETCH_TIMEOUT: int = _config.fetch_timeout
FETCH_BUDGET: int = _config.fetch_budget
FETCH_USER_AGENT: str = _config.fetch_user_agent
MAINTAINER_NAME: str = _config.maintainer_name
# AnyHttpUrl normalizes by appending a trailing slash; strip to match
# pre-typed-config string-equal semantics (existing callers rstrip
# defensively, but make the constant byte-identical for grep stability).
SITE_URL: str = str(_config.site_url).rstrip("/")
# Outbound User-Agent for the analytics/status fetchers (GeoIP DB, FireHOL,
# BetterStack, privacy-relay lists). Derived from SITE_URL so it advertises the
# running site rather than a hardcoded host — follows an env SITE_URL override
# and, post-S3, a dataset-supplied canonical URL. Distinct from FETCH_USER_AGENT
# (the data-feed pipeline's UA).
STATUS_USER_AGENT: str = f"Mozilla/5.0 (compatible; kayak-status; +{SITE_URL})"

# ``MAINTAINER_EMAIL`` module constant was removed in T3.3 closeout —
# no consumers import it (grep -rn 'from kayak.config import' returned
# zero hits on the name). Use ``get_config().maintainer_emails``
# (list[EmailStr]) in new code; PHP reads the same through
# ``Config::list('maintainer_emails')``.
