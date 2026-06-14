"""``levels render-units`` — emit the Batch 4C cutover systemd drop-ins.

Reads ``host.yaml`` and prints (or writes) one
``<unit>.service.d/cutover.conf`` per engine consumer — the drop-ins that
re-point each ``levels``-running unit at the ``/opt/kayak/current`` release
venv. Mirrors ``emit-config``: the tool emits text; the install runbook /
deployer applies it. See ``docs/PLAN_4c_renderers.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kayak.host import load_host_config
from kayak.host_render import engine_unit_names, render_cutover_dropins


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "render-units",
        help="Render the paired-release cutover systemd drop-ins from host.yaml (4C)",
    )
    p.set_defaults(func=render_units)
    p.add_argument(
        "--out-dir",
        type=Path,
        help="Write <unit>.service.d/cutover.conf trees under this dir "
        "(e.g. /etc/systemd/system); default: print a manifest to stdout",
    )
    p.add_argument(
        "--list-units",
        action="store_true",
        help="Print just the engine .service names (one per line) — the units the "
        "cutover re-points; consumed by the deployer's serving-path gate",
    )
    p.add_argument(
        "--host-config",
        type=Path,
        help="host.yaml path (default: $KAYAK_HOST_CONFIG or /etc/kayak/host.yaml)",
    )


def render_units(args: argparse.Namespace) -> int:
    # --list-units is HostConfig-independent (the unit names are fixed), so it must
    # work even when host.yaml is absent/unreadable — the deployer calls it before
    # the host is fully configured.
    if args.list_units:
        for name in engine_unit_names():
            print(name)
        return 0

    try:
        host = load_host_config(args.host_config)
    except ValueError as e:
        print(f"render-units: host config invalid: {e}", file=sys.stderr)
        return 1

    dropins = render_cutover_dropins(host)

    if args.out_dir is None:
        for d in dropins:
            print(f"# ==> {d.path}")
            print(d.text)
        return 0

    # Every drop-in references {release_root}/current (DATASET_DIR, WorkingDirectory,
    # the ExecStart venv), which exists only AFTER the first paired-release
    # activation. Installing them onto a host whose release isn't live yet would
    # point all six consumers at a non-existent dir and break them, so warn — the
    # cutover runbook installs these only once `current` resolves (PR #193 review #3).
    current = Path(host.release_root) / "current"
    if not current.exists():
        print(
            f"render-units: WARNING: {current} does not exist yet — these drop-ins "
            "reference it and will break the consumers if applied before the first "
            "paired-release activation (cutover order: stage+activate, THEN install).",
            file=sys.stderr,
        )

    # NOTE: this writes cutover.conf for the current 6-unit set but does not sweep a
    # stale drop-in if that set ever shrinks; revisit if a consumer is retired.
    out_dir: Path = args.out_dir
    for d in dropins:
        dest = out_dir / d.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(d.text, encoding="utf-8")
        print(f"wrote {dest}")
    return 0
