"""Tests for kayak.utils.struct_log."""

from __future__ import annotations

import io
import json
import logging

from kayak.utils import struct_log
from kayak.utils.struct_log import emit


def test_emit_writes_single_json_line(caplog):
    with caplog.at_level(logging.INFO, logger="kayak.events"):
        emit("step_start", step="fetch", run_id="abc")
    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["event"] == "step_start"
    assert payload["step"] == "fetch"
    assert payload["run_id"] == "abc"
    assert isinstance(payload["ts"], float)


def test_emit_handles_unserializable_fields(caplog):
    """``default=str`` keeps unexpected types from crashing the pipeline."""

    class Custom:
        def __str__(self) -> str:
            return "custom-value"

    with caplog.at_level(logging.INFO, logger="kayak.events"):
        emit("oddball", obj=Custom())
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["obj"] == "custom-value"


def test_emit_survives_default_warning_root(monkeypatch):
    """Regression: events must reach journald under the *production*
    logging config, where ``mkLogger`` leaves the root logger at its
    WARNING default (the pipeline runs ``levels pipeline`` with no
    ``--verbose``). The ``kayak.events`` logger carries its own INFO
    level so the record isn't dropped, and its dedicated handler writes
    bare JSON so recap's ``startswith("{")`` gate accepts it.

    Note this deliberately avoids ``caplog.at_level(..., "kayak.events")``,
    which would *force* the level to INFO and so mask the very bug under
    test (recap saw "Events parsed: 0" / "Pipeline runs: 0").
    """
    # Capture exactly what the dedicated handler writes to journald.
    buf = io.StringIO()
    monkeypatch.setattr(struct_log._logger.handlers[0], "stream", buf)

    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.WARNING)  # mirror mkLogger()'s production default
    try:
        emit("pipeline_start", run_id="r9", steps=["fetch"])
    finally:
        root.setLevel(original_level)

    line = buf.getvalue().strip()
    # Bare JSON — no "<asctime> INFO:" prefix — so journald's MESSAGE
    # starts with '{' and recap.py's parser keeps it.
    assert line.startswith("{"), f"event line not bare JSON: {line!r}"
    payload = json.loads(line)
    assert payload["event"] == "pipeline_start"
    assert payload["run_id"] == "r9"
