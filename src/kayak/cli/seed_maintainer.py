"""Seed or promote an editor row to status='maintainer'.

Usage:
    levels seed-maintainer --email pat@example.com [--name "Pat Welch"]

Idempotent: re-running with the same email is safe. If the editor exists
with a different status, it is promoted to 'maintainer' (except 'banned' —
that must be cleared explicitly).
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from kayak.db.engine import get_session
from kayak.db.models import Editor, EditorStatus

logger = logging.getLogger(__name__)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "seed-maintainer",
        help="Create or promote an editor row to status='maintainer'",
    )
    parser.set_defaults(func=seed_maintainer)
    parser.add_argument("--email", required=True, help="Maintainer email address")
    parser.add_argument("--name", default=None, help="Display name (optional)")


def seed_maintainer(args: argparse.Namespace) -> None:
    email = args.email.strip().lower()
    if "@" not in email:
        print(f"error: '{email}' is not a valid email", file=sys.stderr)
        sys.exit(2)

    session = get_session()
    try:
        ed = session.execute(select(Editor).where(Editor.email == email)).scalar_one_or_none()
        if ed is None:
            ed = Editor(
                email=email,
                display_name=args.name,
                status=EditorStatus.maintainer,
            )
            session.add(ed)
            session.commit()
            print(f"Created maintainer: {email} (id={ed.id})")
            return

        if ed.status == EditorStatus.banned:
            print(
                f"error: editor {email} is banned; clear ban first",
                file=sys.stderr,
            )
            sys.exit(3)

        changed = False
        if ed.status != EditorStatus.maintainer:
            ed.status = EditorStatus.maintainer
            changed = True
        if args.name and ed.display_name != args.name:
            ed.display_name = args.name
            changed = True

        if changed:
            session.commit()
            print(f"Promoted to maintainer: {email} (id={ed.id})")
        else:
            print(f"Already a maintainer: {email} (id={ed.id})")
    finally:
        session.close()
