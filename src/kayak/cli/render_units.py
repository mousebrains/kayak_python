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
from kayak.host_render import render_cutover_dropins


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
        "--host-config",
        type=Path,
        help="host.yaml path (default: $KAYAK_HOST_CONFIG or /etc/kayak/host.yaml)",
    )


def render_units(args: argparse.Namespace) -> int:
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

    out_dir: Path = args.out_dir
    for d in dropins:
        dest = out_dir / d.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(d.text, encoding="utf-8")
        print(f"wrote {dest}")
    return 0
