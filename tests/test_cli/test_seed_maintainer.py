"""Tests for the ``levels seed-maintainer`` CLI."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from kayak.cli.seed_maintainer import seed_maintainer
from kayak.db.models import Editor, EditorStatus


def _run(session, email: str, name: str | None = None) -> None:
    """Invoke seed_maintainer with get_session patched to the test session
    and session.close/commit stubbed so the outer fixture's rollback stays
    intact.
    """
    args = Namespace(email=email, name=name)
    with (
        patch("kayak.cli.seed_maintainer.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", session.flush),  # turn commit into flush
    ):
        seed_maintainer(args)


def test_creates_maintainer_when_missing(session):
    _run(session, "new@example.com", "New Maintainer")

    ed = session.execute(select(Editor).where(Editor.email == "new@example.com")).scalar_one()
    assert ed.status == EditorStatus.maintainer
    assert ed.display_name == "New Maintainer"


def test_idempotent_on_existing_maintainer(session, maintainer):
    before_id = maintainer.id
    before_status = maintainer.status

    _run(session, maintainer.email)

    ed = session.execute(select(Editor).where(Editor.email == maintainer.email)).scalar_one()
    assert ed.id == before_id
    assert ed.status == before_status  # still maintainer, no churn


def test_promotes_pending_editor(session, editor):
    assert editor.status == EditorStatus.pending

    _run(session, editor.email)

    session.refresh(editor)
    assert editor.status == EditorStatus.maintainer


def test_refuses_banned_editor(session, editor):
    editor.status = EditorStatus.banned
    session.flush()

    with pytest.raises(SystemExit) as exc_info:
        _run(session, editor.email)
    assert exc_info.value.code == 3

    # Status must not have been flipped.
    session.refresh(editor)
    assert editor.status == EditorStatus.banned


def test_refuses_invalid_email(session):
    with pytest.raises(SystemExit) as exc_info:
        _run(session, "not-an-email")
    assert exc_info.value.code == 2
