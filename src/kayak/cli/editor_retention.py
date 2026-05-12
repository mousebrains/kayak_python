"""Retention purge for editor_session and editor_magic_link rows.

Usage:
    levels editor-retention                # commit (default)
    levels editor-retention --dry-run      # print counts only
    levels editor-retention --days 180     # override 90-day window

Per `docs/security/decisions.md` D-T4.3:
- 4.3b: editor_magic_link rows where expires_at < now - 90 days → DELETE.
  Conventional incident-discovery window for credential issuance logs.
- 4.3c: editor_session rows where expires_at < now - 90 days → DELETE.
  No FK references editor_session.id, so hard-delete is safe.
- 4.3a (edit_history.changed_by) is OUT OF SCOPE for this command — that
  field's PII linkage is severed at editor-row deletion time, not via
  retention decay. See `levels delete-editor`.

Intended cadence: daily via kayak-editor-retention.timer.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from kayak.db.engine import get_session
from kayak.db.models import EditorMagicLink, EditorSession

logger = logging.getLogger(__name__)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "editor-retention",
        help="Purge expired editor_session + editor_magic_link rows (per D-T4.3)",
    )
    parser.set_defaults(func=editor_retention)
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Delete rows whose expires_at is older than this many days ago (default: 90)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not delete",
    )


def editor_retention(args: argparse.Namespace) -> None:
    if args.days < 1:
        raise ValueError("--days must be >= 1")

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=args.days)
    logger.info(
        "editor-retention cutoff: expires_at < %s (%d days ago)",
        cutoff.isoformat(),
        args.days,
    )

    session = get_session()
    try:
        n_links = session.execute(
            select(func.count())
            .select_from(EditorMagicLink)
            .where(EditorMagicLink.expires_at < cutoff)
        ).scalar_one()
        n_sessions = session.execute(
            select(func.count()).select_from(EditorSession).where(EditorSession.expires_at < cutoff)
        ).scalar_one()

        logger.info("would delete: %d magic-link rows, %d session rows", n_links, n_sessions)

        if args.dry_run:
            print(
                f"[DRY RUN] cutoff={cutoff.isoformat()}: "
                f"{n_links} magic-link rows, {n_sessions} session rows would be deleted"
            )
            return

        if n_links:
            session.execute(delete(EditorMagicLink).where(EditorMagicLink.expires_at < cutoff))
        if n_sessions:
            session.execute(delete(EditorSession).where(EditorSession.expires_at < cutoff))
        session.commit()
        logger.info("deleted %d magic-link rows, %d session rows", n_links, n_sessions)
        print(
            f"editor-retention: deleted {n_links} magic-link rows, "
            f"{n_sessions} session rows (cutoff {cutoff.isoformat()})"
        )
    finally:
        session.close()
