"""Tests for the ``levels editor-retention`` CLI."""

from __future__ import annotations

import hashlib
import os
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from kayak.cli.editor_retention import editor_retention
from kayak.db.models import EditorMagicLink, EditorSession


def _run(session, days: int = 90, dry_run: bool = False) -> None:
    args = Namespace(days=days, dry_run=dry_run)
    with (
        patch("kayak.cli.editor_retention.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", session.flush),
    ):
        editor_retention(args)


def _mk_session(session, editor, *, expires_at: datetime) -> EditorSession:
    es = EditorSession(
        editor_id=editor.id,
        token_hash=hashlib.sha256(os.urandom(32)).hexdigest(),
        expires_at=expires_at,
        ip="127.0.0.1",
        user_agent="test-agent",
    )
    session.add(es)
    session.flush()
    return es


def _mk_magic_link(session, editor, *, expires_at: datetime) -> EditorMagicLink:
    ml = EditorMagicLink(
        editor_id=editor.id,
        token_hash=hashlib.sha256(os.urandom(32)).hexdigest(),
        expires_at=expires_at,
        ip_issued="127.0.0.1",
    )
    session.add(ml)
    session.flush()
    return ml


def test_purges_old_session_rows(session, editor):
    now = datetime.now(UTC).replace(tzinfo=None)
    old = _mk_session(session, editor, expires_at=now - timedelta(days=100))
    new = _mk_session(session, editor, expires_at=now - timedelta(days=10))
    old_id, new_id = old.id, new.id

    _run(session, days=90)

    remaining = {
        row[0]
        for row in session.execute(select(EditorSession.id)).all()
    }
    assert old_id not in remaining
    assert new_id in remaining


def test_purges_old_magic_link_rows(session, editor):
    now = datetime.now(UTC).replace(tzinfo=None)
    old = _mk_magic_link(session, editor, expires_at=now - timedelta(days=100))
    new = _mk_magic_link(session, editor, expires_at=now - timedelta(days=1))
    old_id, new_id = old.id, new.id

    _run(session, days=90)

    remaining = {
        row[0]
        for row in session.execute(select(EditorMagicLink.id)).all()
    }
    assert old_id not in remaining
    assert new_id in remaining


def test_dry_run_does_not_delete(session, editor):
    now = datetime.now(UTC).replace(tzinfo=None)
    _mk_session(session, editor, expires_at=now - timedelta(days=200))
    _mk_magic_link(session, editor, expires_at=now - timedelta(days=200))

    _run(session, days=90, dry_run=True)

    n_sess = session.execute(select(EditorSession)).all()
    n_ml = session.execute(select(EditorMagicLink)).all()
    assert len(n_sess) == 1
    assert len(n_ml) == 1


def test_custom_days_window(session, editor):
    now = datetime.now(UTC).replace(tzinfo=None)
    a = _mk_session(session, editor, expires_at=now - timedelta(days=200))
    b = _mk_session(session, editor, expires_at=now - timedelta(days=120))
    c = _mk_session(session, editor, expires_at=now - timedelta(days=30))

    _run(session, days=150)  # only delete things older than 150 days

    remaining = {
        row[0]
        for row in session.execute(select(EditorSession.id)).all()
    }
    # `a` (200d old) is purged; `b` (120d) and `c` (30d) survive.
    assert a.id not in remaining
    assert b.id in remaining
    assert c.id in remaining


def test_rejects_invalid_days(session, editor):
    with pytest.raises(ValueError):
        _run(session, days=0)
