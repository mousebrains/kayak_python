"""Operator-handled account deletion for the editor pipeline.

Usage:
    levels delete-editor --email pat@example.com           # dry-run
    levels delete-editor --email pat@example.com --yes     # commit

Per `docs/security/decisions.md` D-T4.1: account deletion is operator-
mediated. The user emails the club; the operator runs this script.

The FK chain cascades cleanly:
    editor --CASCADE--> editor_session
    editor --CASCADE--> editor_magic_link
    editor --CASCADE--> maintainer_credential
    editor --CASCADE--> change_request
        change_request --CASCADE--> change_request_attachment
        change_request --SET NULL--> edit_history.change_request_id
    editor --SET NULL--> editor.reviewed_by (cross-row reference)

`edit_history.changed_by` is a free string ('editor:<id>' / 'maintainer:<id>')
NOT a true FK, so it survives editor deletion intentionally — the audit
trail outlives the editor row by design. Pass --anonymize-history to
rewrite those strings to 'deleted:<id>' so the audit trail records the
deletion event but the link back to PII is broken.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import delete, func, select, update

from kayak.db.engine import get_session
from kayak.db.models import (
    ChangeRequest,
    ChangeRequestAttachment,
    EditHistory,
    Editor,
    EditorMagicLink,
    EditorSession,
    MaintainerCredential,
)

logger = logging.getLogger(__name__)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "delete-editor",
        help="Cascade-delete an editor account (operator-handled per D-T4.1)",
    )
    parser.set_defaults(func=delete_editor)
    parser.add_argument("--email", required=True, help="Editor email address")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually commit the deletion (default: dry-run, print counts only)",
    )
    parser.add_argument(
        "--anonymize-history",
        action="store_true",
        help=(
            "Rewrite edit_history.changed_by from 'editor:<id>'/'maintainer:<id>' "
            "to 'deleted:<id>' so the audit trail records the deletion event but "
            "the link to PII is broken. Default: preserve attribution strings."
        ),
    )


def delete_editor(args: argparse.Namespace) -> None:
    email = args.email.strip().lower()
    if "@" not in email:
        print(f"error: '{email}' is not a valid email", file=sys.stderr)
        sys.exit(2)

    session = get_session()
    try:
        ed = session.execute(select(Editor).where(Editor.email == email)).scalar_one_or_none()
        if ed is None:
            print(f"error: no editor found for email '{email}'", file=sys.stderr)
            sys.exit(3)

        editor_id = ed.id
        status = ed.status
        display_name = ed.display_name or "(none)"

        # Tally what will be affected for the dry-run preview.
        n_sessions = session.execute(
            select(func.count())
            .select_from(EditorSession)
            .where(EditorSession.editor_id == editor_id)
        ).scalar_one()
        n_magic_links = session.execute(
            select(func.count())
            .select_from(EditorMagicLink)
            .where(EditorMagicLink.editor_id == editor_id)
        ).scalar_one()
        n_credentials = session.execute(
            select(func.count())
            .select_from(MaintainerCredential)
            .where(MaintainerCredential.editor_id == editor_id)
        ).scalar_one()
        n_change_requests = session.execute(
            select(func.count())
            .select_from(ChangeRequest)
            .where(ChangeRequest.editor_id == editor_id)
        ).scalar_one()
        cr_ids = [
            row[0]
            for row in session.execute(
                select(ChangeRequest.id).where(ChangeRequest.editor_id == editor_id)
            ).all()
        ]
        n_attachments = 0
        if cr_ids:
            n_attachments = session.execute(
                select(func.count())
                .select_from(ChangeRequestAttachment)
                .where(ChangeRequestAttachment.change_request_id.in_(cr_ids))
            ).scalar_one()
        editor_str = f"editor:{editor_id}"
        maint_str = f"maintainer:{editor_id}"
        n_history_attributed = session.execute(
            select(func.count())
            .select_from(EditHistory)
            .where(EditHistory.changed_by.in_([editor_str, maint_str]))
        ).scalar_one()

        print(f"Editor: {email} (id={editor_id}, status={status}, display_name={display_name})")
        print("Will delete (CASCADE):")
        print(f"  editor_session rows         : {n_sessions}")
        print(f"  editor_magic_link rows      : {n_magic_links}")
        print(f"  maintainer_credential rows  : {n_credentials}")
        print(f"  change_request rows         : {n_change_requests}")
        print(f"  change_request_attachment   : {n_attachments}")
        print("  editor row                  : 1")
        print("Will preserve:")
        print(
            f"  edit_history rows attributed to this editor: {n_history_attributed}"
            f" ({'will be anonymized to deleted:<id>' if args.anonymize_history else 'changed_by string preserved as-is'})"
        )

        if not args.yes:
            print("\n[DRY RUN] Pass --yes to commit.")
            return

        # Commit: all-or-nothing in one transaction.
        if args.anonymize_history and n_history_attributed > 0:
            session.execute(
                update(EditHistory)
                .where(EditHistory.changed_by == editor_str)
                .values(changed_by=f"deleted:{editor_id}")
            )
            session.execute(
                update(EditHistory)
                .where(EditHistory.changed_by == maint_str)
                .values(changed_by=f"deleted:{editor_id}")
            )

        # ORM cascade follows the FK relationships declared in models.py.
        session.execute(delete(Editor).where(Editor.id == editor_id))
        session.commit()
        print(f"\nDeleted editor {email} (id={editor_id}) and cascading rows.")
    finally:
        session.close()
