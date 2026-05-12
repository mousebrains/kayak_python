"""Tests for the ``levels delete-editor`` CLI (D-T4.1)."""

from __future__ import annotations

import hashlib
import os
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from kayak.cli.delete_editor import delete_editor
from kayak.db.models import (
    ChangeRequest,
    ChangeStatus,
    ChangeTarget,
    EditHistory,
    Editor,
    EditorMagicLink,
    EditorSession,
)


def _run(session, email: str, yes: bool = False, anonymize: bool = False) -> None:
    args = Namespace(email=email, yes=yes, anonymize_history=anonymize)
    with (
        patch("kayak.cli.delete_editor.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", session.flush),
    ):
        delete_editor(args)


def _populate(session, editor):
    now = datetime.now(UTC).replace(tzinfo=None)
    session.add(
        EditorSession(
            editor_id=editor.id,
            token_hash=hashlib.sha256(os.urandom(32)).hexdigest(),
            expires_at=now + timedelta(days=7),
        )
    )
    session.add(
        EditorMagicLink(
            editor_id=editor.id,
            token_hash=hashlib.sha256(os.urandom(32)).hexdigest(),
            expires_at=now + timedelta(minutes=30),
        )
    )
    cr = ChangeRequest(
        target_type=ChangeTarget.reach,
        target_id=1,
        editor_id=editor.id,
        payload_json='{"foo": "bar"}',
        status=ChangeStatus.pending,
    )
    session.add(cr)
    session.add(
        EditHistory(
            target_type=ChangeTarget.reach,
            target_id=1,
            field="name",
            old_value="A",
            new_value="B",
            changed_by=f"editor:{editor.id}",
        )
    )
    session.flush()


def test_dry_run_does_not_delete(session, editor):
    _populate(session, editor)
    _run(session, editor.email, yes=False)

    assert session.execute(select(Editor).where(Editor.id == editor.id)).scalar_one() is not None
    assert session.execute(select(EditorSession)).all()


def test_yes_deletes_cascade(session, editor):
    _populate(session, editor)
    editor_id = editor.id
    _run(session, editor.email, yes=True)

    assert (
        session.execute(select(Editor).where(Editor.id == editor_id)).scalar_one_or_none() is None
    )
    assert (
        session.execute(select(EditorSession).where(EditorSession.editor_id == editor_id)).all()
        == []
    )
    assert (
        session.execute(select(EditorMagicLink).where(EditorMagicLink.editor_id == editor_id)).all()
        == []
    )
    assert (
        session.execute(select(ChangeRequest).where(ChangeRequest.editor_id == editor_id)).all()
        == []
    )


def test_preserves_audit_trail_by_default(session, editor):
    _populate(session, editor)
    editor_id = editor.id
    _run(session, editor.email, yes=True)

    hist = session.execute(select(EditHistory)).scalars().all()
    assert len(hist) == 1
    assert hist[0].changed_by == f"editor:{editor_id}"


def test_anonymize_flag_rewrites_audit_trail(session, editor):
    _populate(session, editor)
    editor_id = editor.id
    _run(session, editor.email, yes=True, anonymize=True)

    hist = session.execute(select(EditHistory)).scalars().all()
    assert len(hist) == 1
    assert hist[0].changed_by == f"deleted:{editor_id}"


def test_missing_editor_exits(session):
    with pytest.raises(SystemExit) as exc_info:
        _run(session, "nonexistent@example.com", yes=True)
    assert exc_info.value.code == 3


def test_invalid_email_exits(session):
    with pytest.raises(SystemExit) as exc_info:
        _run(session, "not-an-email", yes=True)
    assert exc_info.value.code == 2
