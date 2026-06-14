"""``levels render-serving`` — emit the Batch 4C cutover serving directives.

The two host-specific serving values the cutover changes — the nginx ``root`` and
the PHP-FPM ``open_basedir`` — derived from ``host.yaml`` instead of hand-typed
``sed`` (runbook §5a/§5b). The rest of the serving config (vhost server_names, the
shared cert, the FPM socket/user) is static, committed config the installer
applies from the repo. Mirrors ``emit-config``: emit text; the runbook applies it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kayak.host import load_host_config
from kayak.host_render import render_fpm_open_basedir, render_nginx_root


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "render-serving",
        help="Render the cutover nginx root + PHP-FPM open_basedir from host.yaml (4C)",
    )
    p.set_defaults(func=render_serving)
    p.add_argument(
        "--out-dir",
        type=Path,
        help="Write nginx-levels-docroot.conf + fpm-open-basedir.conf under this "
        "dir; default: print both directives to stdout",
    )
    p.add_argument(
        "--host-config",
        type=Path,
        help="host.yaml path (default: $KAYAK_HOST_CONFIG or /etc/kayak/host.yaml)",
    )


def render_serving(args: argparse.Namespace) -> int:
    try:
        host = load_host_config(args.host_config)
    except ValueError as e:
        print(f"render-serving: host config invalid: {e}", file=sys.stderr)
        return 1

    nginx = render_nginx_root(host)
    fpm = render_fpm_open_basedir(host)

    if args.out_dir is None:
        print("# ==> nginx: root directive for conf/snippets/levels-common.conf")
        print(nginx)
        print("# ==> php-fpm: open_basedir for the kayak pool (pool.d/kayak.conf)")
        print(fpm, end="")
        return 0

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    nginx_path = out_dir / "nginx-levels-docroot.conf"
    fpm_path = out_dir / "fpm-open-basedir.conf"
    nginx_path.write_text(nginx, encoding="utf-8")
    fpm_path.write_text(fpm, encoding="utf-8")
    print(f"wrote {nginx_path}")
    print(f"wrote {fpm_path}")
    return 0
