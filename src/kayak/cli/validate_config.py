"""``levels validate-config`` subcommand.

Phase 3 of `docs/done/PLAN_tier3_closeout.md` § T3.3: construct
``KayakConfig`` against the live env so pydantic surfaces any
out-of-range / unparseable / unknown-extra problems before the deploy
proceeds. Wired into ``scripts/deploy.sh`` between the pip-install
step and ``levels migrate`` — a misconfig fails the deploy without
touching the database.

Optional ``--known-env`` scans the OS env for config-shaped names
(``MAINTAINER_*``, ``HC_*``, ``TURNSTILE_*``, etc.) that don't match
any declared field name or alias. Catches typos like
``MAINTAINER_EMIAL`` that ``extra="forbid"`` doesn't — pydantic-
settings only inspects env vars whose names map to declared fields;
an unmatched typo silently produces the default.

Exit codes (mirror ``scripts/health-check.sh``'s ladder):
    0 — config is valid (warnings printed but non-fatal)
    1 — validation failure (a field's value is wrong) or
        --known-env warnings with ``--strict``
    2 — runner could not run (model import error, etc.)
"""

from __future__ import annotations

import argparse
import os
import sys

from pydantic import AliasChoices, ValidationError

from kayak.config import KayakConfig, require_explicit_site_url_for_publishable_dataset

# Env-var names matching one of these prefixes are checked against the
# allowlist. Prefix-only matching was rejected because ``OUTPUT_FORMAT``
# (a generic operator env) would false-positive — the allowlist below
# is explicit.
_CONFIG_PREFIXES = (
    "DATABASE_",
    "OUTPUT_",
    "FETCH_",
    "MAINTAINER_",
    "SITE_",
    "MAIL_",
    "EDITOR_",
    "TURNSTILE_",
    "HC_",
    "NTFY_",
    "CSP_",
    "AUDIT_",
    "GAUGE_",
    "KAYAK_",
    "DATASET_",
    # The METADATA_DIR alias was removed (R9), but the prefix stays scanned so a
    # stale `METADATA_DIR=` left in a prod .env (or a typo) is flagged for cleanup
    # rather than silently ignored.
    "METADATA_",
    "MAP_LAYERS_",
    "USGS_",
    "SQLITE_",
)

# Env-var names the CLI reads but that don't correspond to a model field
# (reader-side overrides, test scaffolding). These are NEVER consumed by
# ``KayakConfig`` itself.
_EXTRA_KNOWN = frozenset(
    {
        "KAYAK_CONFIG_PATH",  # Phase 1 reader override for /etc/kayak/runtime-config.json
        "KAYAK_LEVELS_BIN",  # Phase 2.3 test scaffolding (tests/php/ConfigTest.php)
        "KAYAK_HOME",  # Phase 5 (T3.4) — declared early so this scan doesn't false-positive
        "KAYAK_DATA",  # scripts/deploy.sh export — path of the kayak_data metadata clone
        "KAYAK_VENV",  # scripts/regenerate_schema_svg.sh dev-side venv override
        # Typed host config (kayak.host — S7/S8 Batch 4): the file override,
        # plus the backup-job knobs the systemd shell scripts read from
        # /etc/kayak/env (their defaults mirror HostConfig's).
        "KAYAK_HOST_CONFIG",
        "KAYAK_BACKUP_DIR",
        "KAYAK_OFFSITE_REMOTE",
        "KAYAK_OFFSITE_KEEP",
        # Read via os.environ by cli/fetch_usgs_ogc.py / gauge_audit/usgs_sites.py.
        # Deliberately NOT a KayakConfig field: emit-config would write it
        # into the www-data-readable runtime-config.json, and PHP has no
        # use for it.
        "USGS_API_KEY",
        # PHP's db.php fallback + health-check.sh's DB override; the
        # python side uses DATABASE_URL, so this is intentionally not a
        # model field (PR #119 review).
        "SQLITE_PATH",
    }
)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "validate-config",
        help="Validate KayakConfig against the live env; warn on unknown env vars",
    )
    parser.set_defaults(func=validate_config)
    parser.add_argument(
        "--known-env",
        action="store_true",
        help="Scan OS env for config-shaped names that aren't declared KayakConfig fields",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on any --known-env warning (default: warn only)",
    )


def _known_env_names() -> set[str]:
    """The set of env-var names ``KayakConfig`` consumes."""
    names: set[str] = set()
    for field_name, info in KayakConfig.model_fields.items():
        names.add(field_name.upper())
        alias = info.validation_alias
        if isinstance(alias, str):
            names.add(alias.upper())
        elif isinstance(alias, AliasChoices):
            # e.g. map_layers_dir reads MAP_LAYERS_DIR or the legacy OSMB_DIR.
            names.update(c.upper() for c in alias.choices if isinstance(c, str))
    return names | _EXTRA_KNOWN


def _scan_unknown_env() -> int:
    """Warn on config-shaped env names that aren't declared fields."""
    warned = 0
    known = _known_env_names()
    for name in sorted(os.environ):
        if not name.startswith(_CONFIG_PREFIXES):
            continue
        if name in known:
            continue
        print(
            f"WARN: env var {name} is not a declared KayakConfig field "
            "(typo? new feature? see src/kayak/config.py)",
            file=sys.stderr,
        )
        warned += 1
    return warned


def _backup_env_knob_errors() -> list[str]:
    """Validate the S8 backup env knobs' values via the HostConfig schema."""
    from kayak.host import HostConfig

    overrides: dict[str, object] = {}
    if (v := os.environ.get("KAYAK_BACKUP_DIR")) is not None:
        overrides["backup_dir"] = v
    if (v := os.environ.get("KAYAK_OFFSITE_REMOTE")) is not None:
        overrides["offsite_remote"] = v
    if (v := os.environ.get("KAYAK_OFFSITE_KEEP")) is not None:
        overrides["offsite_keep"] = v
    if not overrides:
        return []
    try:
        HostConfig(**overrides)  # type: ignore[arg-type]
    except ValueError as e:
        return [str(e)]
    return []


def validate_config(args: argparse.Namespace) -> None:
    try:
        cfg = KayakConfig()
    except ValidationError as e:
        print("ERROR: KayakConfig validation failed", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: validate-config could not run: {e!r}", file=sys.stderr)
        sys.exit(2)

    site_url_errors = require_explicit_site_url_for_publishable_dataset(cfg.dataset_dir)
    if site_url_errors:
        print("ERROR: KayakConfig validation failed", file=sys.stderr)
        for err in site_url_errors:
            print(err, file=sys.stderr)
        sys.exit(1)

    # Typed host config (S7): a malformed /etc/kayak/host.yaml must be a
    # clean deploy-gate failure here — its consumers load it lazily (a bad
    # file otherwise only surfaces when `levels status` runs; PR #189 P2).
    from kayak.host import load_host_config

    try:
        load_host_config()
    except ValueError as e:
        print("ERROR: host config validation failed", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    # Backup env knobs (S8): the shell scripts consume these from
    # /etc/kayak/env, so validate their VALUES here with the same invariants
    # as kayak.host.HostConfig — being a *known name* is not enough
    # (PR #189 review P1: KAYAK_OFFSITE_KEEP=0 would otherwise pass this
    # gate and the offsite prune loop would delete every remote backup; the
    # scripts also fail closed on their own, this catches it at deploy time
    # with a better message).
    host_knob_errors = _backup_env_knob_errors()
    if host_knob_errors:
        print("ERROR: backup env knob validation failed", file=sys.stderr)
        for err in host_knob_errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    warned = _scan_unknown_env() if args.known_env else 0

    if warned and args.strict:
        print(f"validate-config: FAIL ({warned} unknown env var(s))", file=sys.stderr)
        sys.exit(1)
    suffix = f" ({warned} unknown env var warning(s))" if warned else ""
    print(f"validate-config: OK{suffix}")
    sys.exit(0)
