"""CLI coverage for ``levels editor-bridge`` (status + the disabled run-once gate)."""

from __future__ import annotations

from argparse import Namespace

from kayak.cli import editor_bridge as cli
from kayak.config import KayakConfig
from kayak.db.models import BridgeState, ChangeRequest, ChangeRequestBridge, ChangeTarget


def test_run_once_disabled_is_clean_noop(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_config", lambda: KayakConfig(editor_bridge_enabled=False))
    rc = cli.cmd_run_once(Namespace(limit=10))
    assert rc == 0
    assert "disabled" in capsys.readouterr().out


def test_status_prints_counts_by_state(session, editor, monkeypatch, capsys):
    cr = ChangeRequest(
        target_type=ChangeTarget.reach,
        target_id=1,
        editor_id=editor.id,
        payload_json="{}",
        status="approved",
    )
    session.add(cr)
    session.flush()
    session.add(ChangeRequestBridge(change_request_id=cr.id, state=BridgeState.queued))
    session.flush()

    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    cli.cmd_status(Namespace())

    out = capsys.readouterr().out
    assert "queued" in out
    assert "1" in out
