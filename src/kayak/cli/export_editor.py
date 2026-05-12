"""Operator-handled data export for the editor pipeline.

Usage:
    levels export-editor --email pat@example.com                # JSON to stdout
    levels export-editor --email pat@example.com --out file.json

Per `docs/security/decisions.md` D-T4.2: data export is on-request,
operator-handled (pair workflow with D-T4.1 deletion).

The export dump captures everything the editor would reasonably want
back: their account row, every change_request they submitted, and the
slice of edit_history attributed to them (where changed_by matches
'editor:<id>' or 'maintainer:<id>'). The audit-trail slice is included
for completeness; the operator may choose to redact before sending if
the request was just "give me my submissions."
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from kayak.db.engine import get_session
from kayak.db.models import ChangeRequest, EditHistory, Editor

logger = logging.getLogger(__name__)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "export-editor",
        help="Export an editor's account + submissions + audit trail as JSON (per D-T4.2)",
    )
    parser.set_defaults(func=export_editor)
    parser.add_argument("--email", required=True, help="Editor email address")
    parser.add_argument(
        "--out",
        default=None,
        help="Write JSON to this file (default: stdout)",
    )


def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def export_editor(args: argparse.Namespace) -> None:
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

        editor_payload = {
            "id": ed.id,
            "email": ed.email,
            "display_name": ed.display_name,
            "status": str(ed.status),
            "request_note": ed.request_note,
            "created_at": ed.created_at,
            "reviewed_at": ed.reviewed_at,
            "reviewed_by_editor_id": ed.reviewed_by,
            "last_login_at": ed.last_login_at,
        }

        crs = (
            session.execute(
                select(ChangeRequest)
                .where(ChangeRequest.editor_id == ed.id)
                .order_by(ChangeRequest.submitted_at)
            )
            .scalars()
            .all()
        )
        cr_payload = [
            {
                "id": cr.id,
                "target_type": str(cr.target_type),
                "target_id": cr.target_id,
                "submitted_at": cr.submitted_at,
                "subject": cr.subject,
                "payload_json": cr.payload_json,
                "notes_to_maint": cr.notes_to_maint,
                "status": str(cr.status),
                "reviewed_at": cr.reviewed_at,
                "reviewed_by_editor_id": cr.reviewed_by,
                "reviewer_note": cr.reviewer_note,
                "applied_json": cr.applied_json,
                "source_url": cr.source_url,
            }
            for cr in crs
        ]

        editor_str = f"editor:{ed.id}"
        maint_str = f"maintainer:{ed.id}"
        hist = (
            session.execute(
                select(EditHistory)
                .where(EditHistory.changed_by.in_([editor_str, maint_str]))
                .order_by(EditHistory.changed_at)
            )
            .scalars()
            .all()
        )
        hist_payload = [
            {
                "id": h.id,
                "target_type": str(h.target_type),
                "target_id": h.target_id,
                "change_request_id": h.change_request_id,
                "field": h.field,
                "old_value": h.old_value,
                "new_value": h.new_value,
                "changed_at": h.changed_at,
                "changed_by": h.changed_by,
            }
            for h in hist
        ]

        export = {
            "exported_at": datetime.now(UTC).replace(tzinfo=None),
            "schema_note": (
                "Per docs/security/decisions.md D-T4.2. payload_json/applied_json "
                "are raw JSON strings as stored; their structure depends on "
                "target_type."
            ),
            "editor": editor_payload,
            "change_requests": cr_payload,
            "edit_history_attributed_to_editor": hist_payload,
        }

        out_text = json.dumps(export, default=_serialize, indent=2)
        if args.out:
            with open(args.out, "w") as f:
                f.write(out_text)
            print(f"Wrote export for {email} to {args.out}", file=sys.stderr)
        else:
            print(out_text)
    finally:
        session.close()
