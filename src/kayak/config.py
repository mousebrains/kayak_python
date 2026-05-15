"""Application configuration.

The typed ``KayakConfig`` (pydantic-settings) is the source of truth.
Module-level constants below it are derived for source-compat with the
existing ``from kayak.config import DATABASE_URL`` call pattern; new
code should use ``get_config()`` (or instantiate ``KayakConfig()``
directly when test-monkeypatching env vars).

Phase 0 of `docs/PLAN_tier3_closeout.md` § T3.3: the schema lands; no
read-path consumers move yet. Phases 1-4 introduce ``levels
emit-config``, the PHP read path, and finally remove the module-level
constants.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import AnyHttpUrl, EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"


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
    hc_metadata_snapshot: AnyHttpUrl | None = None
    hc_recap: AnyHttpUrl | None = None

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


_config = get_config()

# Module-level constants derived from the model — preserved for source-
# compat with existing ``from kayak.config import X`` callers. Phase 4
# of the typed-config plan removes these once all callers move to
# ``get_config()``.
DATABASE_URL: str = _config.database_url
OUTPUT_DIR: str = str(_config.output_dir)
FETCH_TIMEOUT: int = _config.fetch_timeout
FETCH_BUDGET: int = _config.fetch_budget
FETCH_USER_AGENT: str = _config.fetch_user_agent
MAINTAINER_NAME: str = _config.maintainer_name
# AnyHttpUrl normalizes by appending a trailing slash; strip to match
# pre-typed-config string-equal semantics (existing callers rstrip
# defensively, but make the constant byte-identical for grep stability).
SITE_URL: str = str(_config.site_url).rstrip("/")

# ``MAINTAINER_EMAIL`` preserves the single-string contract from before
# the typed model (Phase 0 = no behavior change). New code should use
# ``get_config().maintainer_emails`` (list[EmailStr]); the hardcoded
# fallback below is removed in Phase 2 alongside the PHP read-path
# migration.
MAINTAINER_EMAIL: str = os.environ.get("MAINTAINER_EMAIL", "pat.kayak@gmail.com")
