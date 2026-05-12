"""Tests for the ``levels export-editor`` CLI (D-T4.2)."""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import patch

import pytest

from kayak.cli.export_editor import export_editor
from kayak.db.models import ChangeRequest, ChangeStatus, ChangeTarget, EditHistory


def _run(session, email: str, out: str | None = None) -> None:
    args = Namespace(email=email, out=out)
    with (
        patch("kayak.cli.export_editor.get_session", return_value=session),
        patch.object(session, "close", lambda: None),
        patch.object(session, "commit", session.flush),
    ):
        export_editor(args)


def test_exports_editor_with_no_data(session, editor, capsys):
    _run(session, editor.email)

    out = json.loads(capsys.readouterr().out)
    assert out["editor"]["email"] == editor.email
    assert out["editor"]["id"] == editor.id
    assert out["change_requests"] == []
    assert out["edit_history_attributed_to_editor"] == []
    assert "exported_at" in out


def test_exports_change_requests_and_history(session, editor, capsys):
    cr = ChangeRequest(
        target_type=ChangeTarget.reach,
        target_id=42,
        editor_id=editor.id,
        payload_json='{"foo": "bar"}',
        subject="test proposal",
        status=ChangeStatus.pending,
    )
    session.add(cr)
    session.add(
        EditHistory(
            target_type=ChangeTarget.reach,
            target_id=42,
            field="name",
            old_value="A",
            new_value="B",
            changed_by=f"editor:{editor.id}",
        )
    )
    session.flush()

    _run(session, editor.email)
    out = json.loads(capsys.readouterr().out)

    assert len(out["change_requests"]) == 1
    assert out["change_requests"][0]["subject"] == "test proposal"
    assert out["change_requests"][0]["payload_json"] == '{"foo": "bar"}'

    assert len(out["edit_history_attributed_to_editor"]) == 1
    assert out["edit_history_attributed_to_editor"][0]["field"] == "name"


def test_excludes_other_editors_history(session, editor, maintainer, capsys):
    session.add(
        EditHistory(
            target_type=ChangeTarget.reach,
            target_id=1,
            field="name",
            new_value="other",
            changed_by=f"editor:{maintainer.id}",  # belongs to a different editor
        )
    )
    session.flush()

    _run(session, editor.email)
    out = json.loads(capsys.readouterr().out)

    assert out["edit_history_attributed_to_editor"] == []


def test_writes_to_file(session, editor, tmp_path):
    out_path = tmp_path / "export.json"
    _run(session, editor.email, out=str(out_path))

    data = json.loads(out_path.read_text())
    assert data["editor"]["email"] == editor.email


def test_missing_editor_exits(session):
    with pytest.raises(SystemExit) as exc_info:
        _run(session, "nonexistent@example.com")
    assert exc_info.value.code == 3


def test_invalid_email_exits(session):
    with pytest.raises(SystemExit) as exc_info:
        _run(session, "not-an-email")
    assert exc_info.value.code == 2
