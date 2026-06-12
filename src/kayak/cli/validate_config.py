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
    "KAYAK_",
    "DATASET_",
    "METADATA_",  # deprecated alias of DATASET_DIR (S6.1) — kept one release
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
        # Read via os.environ by fetch_usgs_ogc.py / fetch_usgs_sites.py.
        # Deliberately NOT a KayakConfig field: emit-config would write it
        # into the www-data-readable runtime-config.json, and PHP has no
        # use for it.
        "USGS_API_KEY",
        # PHP's db.php fallback + health-check.sh's DB override; the
        # python side uses DATABASE_URL, so this is intentionally not a
        # model field (PR #119 review — same typo class as METADATA_DIR).
        "SQLITE_PATH",
        # Retired in SA-teardown-B (the kayak-metadata-snapshot unit and its
        # hc_metadata_snapshot field are gone). Tolerated for ONE release so a
        # stale `HC_METADATA_SNAPSHOT=` line left in a prod `.env` /
        # `/etc/kayak/env` can't fail `deploy.sh`'s `validate-config --known-env
        # --strict` gate (it exits 1 on any unknown `HC_*` name) and brick an
        # otherwise-safe deploy. Remove this entry — and the `.env` line — once
        # the deploy carrying SA-teardown-B has landed.
        "HC_METADATA_SNAPSHOT",
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
            # e.g. dataset_dir reads DATASET_DIR or the legacy METADATA_DIR.
            names.update(c.upper() for c in alias.choices if isinstance(c, str))
    return names | _EXTRA_KNOWN


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

    warned = 0
    if args.known_env:
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

    if warned and args.strict:
        print(f"validate-config: FAIL ({warned} unknown env var(s))", file=sys.stderr)
        sys.exit(1)
    suffix = f" ({warned} unknown env var warning(s))" if warned else ""
    print(f"validate-config: OK{suffix}")
    sys.exit(0)
