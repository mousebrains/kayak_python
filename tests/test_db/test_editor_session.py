"""Editor session lifecycle — mirrors the lookup the PHP current_editor() does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from kayak.db.models import Editor, EditorSession, EditorStatus


def _current_editor_query(session, token_hash: str):
    """Python mirror of php/includes/auth.php::current_editor() SQL.

    Returns the Editor row if the session token is valid (unrevoked,
    unexpired, editor not banned), else None.
    """
    stmt = (
        select(Editor, EditorSession)
        .join(EditorSession, Editor.id == EditorSession.editor_id)
        .where(
            EditorSession.token_hash == token_hash,
            EditorSession.revoked_at.is_(None),
            EditorSession.expires_at > datetime.now(UTC),
            Editor.status != EditorStatus.banned,
        )
    )
    return session.execute(stmt).first()


def test_active_session_resolves(session, editor_session):
    row = _current_editor_query(session, editor_session.token_hash)
    assert row is not None


def test_expired_session_rejected(session, editor_session):
    editor_session.expires_at = datetime.now(UTC) - timedelta(hours=1)
    session.flush()

    row = _current_editor_query(session, editor_session.token_hash)
    assert row is None


def test_revoked_session_rejected(session, editor_session):
    editor_session.revoked_at = datetime.now(UTC)
    session.flush()

    row = _current_editor_query(session, editor_session.token_hash)
    assert row is None


def test_banned_editor_session_rejected(session, editor, editor_session):
    editor.status = EditorStatus.banned
    session.flush()

    row = _current_editor_query(session, editor_session.token_hash)
    assert row is None
