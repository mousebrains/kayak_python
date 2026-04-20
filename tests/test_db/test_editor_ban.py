"""Ban workflow — banning an editor must revoke their sessions.

Mirrors the admin.php path: UPDATE editor SET status='banned' WHERE id=?;
UPDATE editor_session SET revoked_at=now() WHERE editor_id=? AND revoked_at IS NULL;
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from kayak.db.models import Editor, EditorSession, EditorStatus


def test_ban_revokes_active_sessions(session, editor):
    now = datetime.now(UTC)
    # Two active sessions for this editor.
    s1 = EditorSession(
        editor_id=editor.id,
        token_hash="1" * 64,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    s2 = EditorSession(
        editor_id=editor.id,
        token_hash="2" * 64,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    session.add_all([s1, s2])
    session.flush()

    # Ban + revoke (the two-statement admin path).
    session.execute(update(Editor).where(Editor.id == editor.id).values(status=EditorStatus.banned))
    session.execute(
        update(EditorSession)
        .where(
            EditorSession.editor_id == editor.id,
            EditorSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    session.flush()

    # Both sessions now have revoked_at set.
    revoked = (
        session.execute(
            select(EditorSession.revoked_at).where(EditorSession.editor_id == editor.id)
        )
        .scalars()
        .all()
    )
    assert len(revoked) == 2
    assert all(r is not None for r in revoked)

    # The pending-session lookup from current_editor returns nothing.
    found = session.execute(
        select(EditorSession).where(
            EditorSession.editor_id == editor.id,
            EditorSession.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    assert found is None
