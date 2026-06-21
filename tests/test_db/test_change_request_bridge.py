"""ChangeRequestBridge schema invariants (Tier 1 of the editor → kayak_data PR bridge).

The bridge row is engine runtime state: one per endorsed change_request, gone when
the request is, and at most one per request. These guard the column defaults and the
two FK rules the worker relies on (UNIQUE per request, CASCADE delete, queued_by
SET NULL).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from kayak.db.models import BridgeState, ChangeRequest, ChangeRequestBridge, ChangeTarget


def _make_cr(session, editor_id: int, target_id: int = 42) -> ChangeRequest:
    cr = ChangeRequest(
        target_type=ChangeTarget.reach,
        target_id=target_id,
        editor_id=editor_id,
        subject="update description",
        payload_json='{"reach": {"description": "new text"}}',
    )
    session.add(cr)
    session.flush()
    return cr


def test_bridge_defaults(session, editor, maintainer):
    cr = _make_cr(session, editor.id)
    bridge = ChangeRequestBridge(change_request_id=cr.id, queued_by=maintainer.id)
    session.add(bridge)
    session.flush()
    assert bridge.state == BridgeState.queued
    assert bridge.attempt == 1
    assert bridge.queued_at is not None
    # everything the worker fills later starts empty
    assert bridge.branch_name is None
    assert bridge.pr_number is None
    assert bridge.pr_merge_sha is None
    assert bridge.lease_owner is None


def test_bridge_unique_per_change_request(session, editor, maintainer):
    cr = _make_cr(session, editor.id)
    session.add(ChangeRequestBridge(change_request_id=cr.id, queued_by=maintainer.id))
    session.flush()
    # A second bridge row for the same request violates the UNIQUE constraint —
    # the worker must reuse the existing row, never create a duplicate.
    session.add(ChangeRequestBridge(change_request_id=cr.id, queued_by=maintainer.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_bridge_cascade_deletes_with_change_request(session, editor, maintainer):
    cr = _make_cr(session, editor.id)
    session.add(ChangeRequestBridge(change_request_id=cr.id, queued_by=maintainer.id))
    session.flush()
    session.delete(cr)
    session.flush()
    assert session.scalars(select(ChangeRequestBridge)).all() == []


def test_bridge_queued_by_set_null_on_editor_delete(session, editor, maintainer):
    cr = _make_cr(session, editor.id)
    bridge = ChangeRequestBridge(change_request_id=cr.id, queued_by=maintainer.id)
    session.add(bridge)
    session.flush()
    # Deleting the maintainer who queued it nulls the pointer but keeps the row.
    session.delete(maintainer)
    session.flush()
    session.refresh(bridge)
    assert bridge.queued_by is None
    assert session.get(ChangeRequestBridge, bridge.id) is not None
