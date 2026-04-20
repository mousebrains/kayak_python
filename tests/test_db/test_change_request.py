"""ChangeRequest state-transition invariants.

Mirrors the SQL predicates the PHP review / propose flow uses to make sure
the state machine can't be re-entered after approval/rejection.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update

from kayak.db.models import ChangeRequest, ChangeStatus, ChangeTarget


def _make_pending(session, editor_id: int, target_id: int = 42) -> ChangeRequest:
    cr = ChangeRequest(
        target_type=ChangeTarget.reach,
        target_id=target_id,
        editor_id=editor_id,
        subject="update description",
        payload_json='{"description": "new text"}',
    )
    session.add(cr)
    session.flush()
    return cr


def test_pending_default_status(session, editor):
    cr = _make_pending(session, editor.id)
    assert cr.status == ChangeStatus.pending
    assert cr.reviewed_at is None
    assert cr.reviewed_by is None


def test_approve_only_from_pending(session, editor, maintainer):
    cr = _make_pending(session, editor.id)

    # Approve — mirrors review.php transition (status=pending guard).
    now = datetime.now(UTC)
    updated = session.execute(
        update(ChangeRequest)
        .where(
            ChangeRequest.id == cr.id,
            ChangeRequest.status == ChangeStatus.pending,
        )
        .values(
            status=ChangeStatus.approved,
            reviewed_at=now,
            reviewed_by=maintainer.id,
        )
    ).rowcount
    assert updated == 1

    session.refresh(cr)
    assert cr.status == ChangeStatus.approved
    assert cr.reviewed_by == maintainer.id

    # Second approval attempt must be a no-op — the pending predicate fails.
    second = session.execute(
        update(ChangeRequest)
        .where(
            ChangeRequest.id == cr.id,
            ChangeRequest.status == ChangeStatus.pending,
        )
        .values(status=ChangeStatus.approved)
    ).rowcount
    assert second == 0


def test_rejected_cannot_be_re_approved(session, editor, maintainer):
    cr = _make_pending(session, editor.id)
    cr.status = ChangeStatus.rejected
    cr.reviewed_at = datetime.now(UTC)
    cr.reviewed_by = maintainer.id
    session.flush()

    # Try to approve via the pending-guarded UPDATE — should touch 0 rows.
    rows = session.execute(
        update(ChangeRequest)
        .where(
            ChangeRequest.id == cr.id,
            ChangeRequest.status == ChangeStatus.pending,
        )
        .values(status=ChangeStatus.approved)
    ).rowcount
    assert rows == 0

    # Row stays in its rejected state.
    found = session.execute(select(ChangeRequest).where(ChangeRequest.id == cr.id)).scalar_one()
    assert found.status == ChangeStatus.rejected
