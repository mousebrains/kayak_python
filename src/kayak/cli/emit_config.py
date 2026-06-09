"""``levels emit-config`` + ``levels show-config`` subcommands.

Phase 1 of `docs/done/PLAN_tier3_closeout.md` § T3.3: write the resolved
``KayakConfig`` to ``/etc/kayak/runtime-config.json`` (mode 0640
root:www-data) so PHP and any future consumers read a single
typed source of truth instead of each component re-doing
``getenv()`` calls.

The JSON file is the consumer surface; ``levels show-config`` prints
the same content to stdout for human inspection. Tests can override
the default output path via ``--out`` or the ``KAYAK_CONFIG_PATH`` env
override (production deploys never set the env override).

Secrets in ``KayakConfig`` are pydantic ``SecretStr`` fields;
``model_dump(mode="json")`` would mask them as ``"**********"``. We
manually unwrap via ``.get_secret_value()`` because the JSON file is
mode 0640 root:www-data — readable only by root and php-fpm.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from kayak.config import KayakConfig
from kayak.dataset.site import load_site_config

DEFAULT_OUTPUT_PATH = "/etc/kayak/runtime-config.json"
"""Where the JSON snapshot lives on a production host."""

DEFAULT_MODE = 0o640
"""File mode bits applied after the atomic rename."""

DEFAULT_GROUP = "www-data"
"""POSIX group that owns the file when running as root."""


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``emit-config`` and ``show-config`` subcommands."""
    emit = subparsers.add_parser(
        "emit-config",
        help="Write the resolved KayakConfig to /etc/kayak/runtime-config.json",
    )
    emit.set_defaults(func=emit_config)
    emit.add_argument(
        "--out",
        default=os.environ.get("KAYAK_CONFIG_PATH", DEFAULT_OUTPUT_PATH),
        help="Output JSON path (default: $KAYAK_CONFIG_PATH or %(default)s)",
    )
    emit.add_argument(
        "--dry-run",
        action="store_true",
        help="Write to stdout instead of the output path",
    )

    show = subparsers.add_parser(
        "show-config",
        help="Print the resolved KayakConfig to stdout (table or JSON)",
    )
    show.set_defaults(func=show_config)
    show.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format (default: %(default)s)",
    )


def build_config_data(cfg: KayakConfig) -> dict[str, Any]:
    """Render ``cfg`` as a JSON-serializable dict with secrets unwrapped.

    ``model_dump(mode="json", exclude_none=True)`` is the base; we then
    walk the model's fields and overwrite any ``SecretStr`` value with
    its plaintext form. ``exclude_none`` keeps the JSON tight — fields
    that are ``None`` (most ``hc_*`` URLs on a dev box) are omitted
    rather than written as ``null``.

    Derived keys for PHP consumers are added at the end:
    - ``database_path``: the SQLite filesystem path, stripped of the
      ``sqlite:///`` URL prefix. PHP's PDO constructor wants a path,
      not a SQLAlchemy URL.
    """
    data = cfg.model_dump(mode="json", exclude_none=True)
    for name in type(cfg).model_fields:
        if name not in data:
            continue
        raw = getattr(cfg, name)
        if isinstance(raw, SecretStr):
            data[name] = raw.get_secret_value()

    db_url = data.get("database_url")
    if isinstance(db_url, str) and db_url.startswith("sqlite:///"):
        data["database_path"] = db_url.removeprefix("sqlite:///")

    # Resolved dataset site identity (S3a) so PHP reads branding from the same
    # typed source as the static build. Engine defaults when the dataset has no
    # site.yaml; a malformed one would already have failed validate-dataset.
    data["site"] = load_site_config(cfg.dataset_dir).model_dump(mode="json")

    return data


def _render_json(data: dict[str, Any]) -> str:
    """Stable serialization: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, content: str, mode: int = DEFAULT_MODE) -> None:
    """Write ``content`` to ``path`` atomically + apply mode/owner bits.

    The ``.tmp`` file lives in the SAME directory as the target so
    ``os.rename`` is atomic — rename across filesystems is NOT atomic
    and would leave a half-written file readable on failure. ``chown
    root:www-data`` runs only when the process is root (production
    deploy via ``sudo -n``); in dev/test the file ends up caller-owned.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, mode)
    if os.geteuid() == 0:
        try:
            gid = grp.getgrnam(DEFAULT_GROUP).gr_gid
            os.chown(tmp, 0, gid)
        except KeyError:
            # ``www-data`` not present (custom host); leave default ownership.
            pass
    os.replace(tmp, path)


def emit_config(args: argparse.Namespace) -> None:
    """Write the resolved KayakConfig JSON snapshot."""
    cfg = KayakConfig()
    data = build_config_data(cfg)
    content = _render_json(data)

    if args.dry_run:
        sys.stdout.write(content)
        return

    out_path = Path(args.out)
    if out_path.exists() and out_path.read_text(encoding="utf-8") == content:
        print(f"{out_path}: unchanged")
        return

    _atomic_write(out_path, content)
    print(f"{out_path}: updated")


def show_config(args: argparse.Namespace) -> None:
    """Print the resolved KayakConfig to stdout for human inspection."""
    cfg = KayakConfig()
    data = build_config_data(cfg)

    if args.format == "json":
        sys.stdout.write(_render_json(data))
        return

    # Table mode: align field names + values in two columns. Fields
    # present in the model but absent from ``data`` (because their value
    # is ``None``) are shown as ``(unset)`` so the human reader sees the
    # full surface, not just the populated subset.
    width = max(len(name) for name in type(cfg).model_fields)
    for name in type(cfg).model_fields:
        if name in data:
            rendered = data[name]
            if isinstance(rendered, list):
                rendered = ", ".join(str(x) for x in rendered) or "(empty list)"
            print(f"  {name:<{width}}  {rendered}")
        else:
            print(f"  {name:<{width}}  (unset)")
