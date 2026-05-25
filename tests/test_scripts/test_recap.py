"""Unit tests for scripts/recap.py _summarize.

The script lives outside src/ so we import it via importlib path. The
subprocess-driven journalctl read is exercised by hand; this test only
covers the pure formatting path.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
from pathlib import Path

_RECAP_PATH = Path(__file__).resolve().parents[2] / "scripts" / "recap.py"


def _load_recap():
    spec = importlib.util.spec_from_file_location("recap", _RECAP_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_journalctl_json_parses_real_struct_log_line(monkeypatch):
    """End-to-end seam guard: the bare-JSON line ``struct_log.emit()``
    writes is exactly what ``_journalctl_json`` expects in journald's
    ``MESSAGE``.

    This crosses the producer/consumer boundary that the formatter-prefix
    bug lived in — events arriving as ``"<asctime> INFO: {json}"`` were
    silently dropped by the ``startswith("{")`` gate, so recap reported
    "Events parsed: 0" while the pipeline ran fine. No prior test
    exercised ``_journalctl_json`` against an actual emitted line.
    """
    from kayak.utils import struct_log

    recap = _load_recap()

    # Reproduce a genuine emit() line by capturing the dedicated handler.
    buf = io.StringIO()
    monkeypatch.setattr(struct_log._logger.handlers[0], "stream", buf)
    logging.getLogger().setLevel(logging.WARNING)  # production default
    struct_log.emit("step_done", run_id="r1", step="build", elapsed_s=1.0, outcome="ok")
    message = buf.getvalue().strip()

    # Wrap it the way `journalctl --output json` presents each entry,
    # then drive recap's real parser via a stubbed subprocess.
    journal_line = json.dumps({"MESSAGE": message, "_SYSTEMD_UNIT": "kayak-pipeline.service"})

    class _Proc:
        stdout = journal_line + "\n"
        stderr = ""

    monkeypatch.setattr(recap.subprocess, "run", lambda *a, **k: _Proc())

    events = recap._journalctl_json("kayak-*", since="7 days ago")

    assert len(events) == 1, "emitted line was dropped by the parse gate"
    assert events[0]["event"] == "step_done"
    assert events[0]["step"] == "build"
    assert events[0]["_unit"] == "kayak-pipeline.service"


def test_summarize_empty_events_returns_no_events_marker():
    recap = _load_recap()
    out = recap._summarize([])
    assert "Pipeline runs: 0" in out
    assert "(no events found)" not in out  # The header always renders


def test_summarize_one_clean_run():
    recap = _load_recap()
    events = [
        {
            "event": "pipeline_start",
            "ts": 100.0,
            "run_id": "r1",
            "steps": ["fetch", "build"],
        },
        {"event": "step_start", "ts": 101.0, "run_id": "r1", "step": "fetch"},
        {
            "event": "step_done",
            "ts": 102.5,
            "run_id": "r1",
            "step": "fetch",
            "outcome": "ok",
            "elapsed_s": 1.5,
            "error": None,
        },
        {"event": "step_start", "ts": 103.0, "run_id": "r1", "step": "build"},
        {
            "event": "step_done",
            "ts": 104.0,
            "run_id": "r1",
            "step": "build",
            "outcome": "ok",
            "elapsed_s": 1.0,
            "error": None,
        },
        {
            "event": "pipeline_done",
            "ts": 104.5,
            "run_id": "r1",
            "elapsed_s": 4.5,
            "ok": 2,
            "failed": 0,
            "skipped": 0,
        },
    ]
    out = recap._summarize(events)
    assert "Pipeline runs: 1" in out
    assert "step totals — ok 2" in out
    assert "fetch" in out
    assert "build" in out
    # No failure block when no step failed.
    assert "Most recent failure per step" not in out


def test_summarize_records_failure_and_skip():
    recap = _load_recap()
    events = [
        {"event": "pipeline_start", "ts": 100.0, "run_id": "r1", "steps": ["a", "b"]},
        {
            "event": "step_failed",
            "ts": 101.0,
            "run_id": "r1",
            "step": "a",
            "outcome": "failed",
            "elapsed_s": 0.5,
            "error": "boom",
        },
        {
            "event": "step_skipped",
            "ts": 101.1,
            "run_id": "r1",
            "step": "b",
            "reason": "upstream_failed",
        },
        {
            "event": "pipeline_done",
            "ts": 101.5,
            "run_id": "r1",
            "elapsed_s": 1.5,
            "ok": 0,
            "failed": 1,
            "skipped": 1,
        },
    ]
    out = recap._summarize(events)
    assert "failed 1" in out
    assert "skipped 1" in out
    assert "Most recent failure per step" in out
    assert "a: boom" in out
