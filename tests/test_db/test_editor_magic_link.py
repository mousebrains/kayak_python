"""Magic-link single-use semantics — mirrors consume_magic_link() in PHP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from kayak.db.models import EditorMagicLink


def _consume(session, token_hash: str) -> EditorMagicLink | None:
    """Python mirror of php/includes/auth.php::consume_magic_link() intent.

    Find an unused, unexpired link, mark used_at, return the row.
    The atomicity is what matters — a second call against the same token
    must find nothing because used_at is now set.
    """
    row = session.execute(
        select(EditorMagicLink).where(
            EditorMagicLink.token_hash == token_hash,
            EditorMagicLink.used_at.is_(None),
            EditorMagicLink.expires_at > datetime.now(UTC),
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    session.execute(
        update(EditorMagicLink)
        .where(
            EditorMagicLink.id == row.id,
            EditorMagicLink.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )
    session.flush()
    return row


def test_fresh_link_consumes(session, magic_link):
    row = _consume(session, magic_link.token_hash)
    assert row is not None
    assert row.id == magic_link.id

    session.refresh(magic_link)
    assert magic_link.used_at is not None


def test_link_is_single_use(session, magic_link):
    first = _consume(session, magic_link.token_hash)
    assert first is not None

    second = _consume(session, magic_link.token_hash)
    assert second is None, "a used magic link must not consume twice"


def test_expired_link_rejected(session, magic_link):
    magic_link.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.flush()

    row = _consume(session, magic_link.token_hash)
    assert row is None
