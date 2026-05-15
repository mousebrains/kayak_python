"""Tests for kayak.utils.struct_log."""

from __future__ import annotations

import json
import logging

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
