"""Tests for the LI-COR public-dashboard parser.

Covers the pure ``parse_records`` contract (no session) plus the DB-write path
via ``parse`` (session fixture). Channels are matched by UUID from the
configured URL's query params; the response's display ``metricName`` /
``metricUnits`` are ignored.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from kayak.db.models import DataType, Observation
from kayak.parsers.base import ObservationRecord
from kayak.parsers.licor import LicorParser, channel_map

# Pin "now" so the future-timestamp guard is deterministic.
_PINNED_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

_FLOW_UUID = "flow-uuid-1111"
_LEVEL_UUID = "level-uuid-2222"
_TEMP_UUID = "temp-uuid-3333"
_AIR_UUID = "air-uuid-9999"  # present on the dashboard, never requested

_CONFIG_URL = (
    "https://www.licor.cloud/api/v2/timeseriesdata"
    "?dashboardUUID=DASH-abc"
    f"&flow={_FLOW_UUID}&gauge={_LEVEL_UUID}&temperature={_TEMP_UUID}"
    "&last=2&unit=days&interval=15&intervalUnit=minutes"
)

# 1700000000000 ms = 2023-11-14T22:13:20Z; 1700000900000 = +15 min.
_TS1_MS = 1700000000000
_TS2_MS = 1700000900000
_TS1 = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
_TS2 = datetime(2023, 11, 14, 22, 28, 20, tzinfo=UTC)


def _record(channel_uuid: str, metric_name: str, units: str, valid: list) -> dict:
    return {
        "channelUUID": channel_uuid,
        "metricName": metric_name,
        "metricUnits": units,
        "datum": {"valid": valid, "error": []},
    }


def _payload(records: list[dict]) -> str:
    return json.dumps({"success": True, "value": {"records": records}})


def _new_parser(url: str = _CONFIG_URL) -> LicorParser:
    """Construct the parser without a session — parse_records must not use it."""
    return LicorParser(url=url, session=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# channel_map
# ---------------------------------------------------------------------------


def test_channel_map_reads_query_params():
    assert channel_map(_CONFIG_URL) == {
        _FLOW_UUID: DataType.flow,
        _LEVEL_UUID: DataType.gauge,
        _TEMP_UUID: DataType.temperature,
    }


def test_channel_map_missing_param_omitted():
    url = "https://www.licor.cloud/api/v2/timeseriesdata?flow=" + _FLOW_UUID
    assert channel_map(url) == {_FLOW_UUID: DataType.flow}


def test_channel_map_duplicate_uuid_excluded_not_mistyped():
    """A UUID claimed by two params is dropped, not silently assigned one type."""
    url = f"https://www.licor.cloud/api/v2/timeseriesdata?flow={_FLOW_UUID}&gauge={_FLOW_UUID}&temperature={_TEMP_UUID}"
    # _FLOW_UUID is ambiguous (flow vs gauge) → omitted entirely; temp survives.
    assert channel_map(url) == {_TEMP_UUID: DataType.temperature}


# ---------------------------------------------------------------------------
# parse_records — pure contract
# ---------------------------------------------------------------------------


def test_parse_records_three_channels():
    """Each requested channel maps to its DataType; units/metricName are ignored."""
    payload = _payload(
        [
            _record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, 305.0]]),
            _record(_LEVEL_UUID, "Water Level", "feet", [[_TS1_MS, 6.85]]),
            # Note the degree-symbol unit string the real API returns — proving
            # we never key off metricUnits.
            _record(_TEMP_UUID, "Water Temperature", "°F", [[_TS1_MS, 57.3]]),
        ]
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("DASH-abc", DataType.flow, _TS1, 305.0),
        ObservationRecord("DASH-abc", DataType.gauge, _TS1, 6.85),
        ObservationRecord("DASH-abc", DataType.temperature, _TS1, 57.3),
    ]


def test_parse_records_multiple_points_per_channel():
    payload = _payload([_record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, 1.0], [_TS2_MS, 2.0]])])
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("DASH-abc", DataType.flow, _TS1, 1.0),
        ObservationRecord("DASH-abc", DataType.flow, _TS2, 2.0),
    ]


def test_air_temperature_channel_ignored():
    """A record whose channelUUID isn't in the configured URL is skipped cleanly."""
    payload = _payload(
        [
            _record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, 305.0]]),
            _record(_AIR_UUID, "Air Temperature", "°F", [[_TS1_MS, 72.0]]),
        ]
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [ObservationRecord("DASH-abc", DataType.flow, _TS1, 305.0)]


def test_epoch_ms_converted_to_utc():
    payload = _payload([_record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, 100.0]])])
    (rec,) = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert rec.observed_at == _TS1
    assert rec.observed_at.tzinfo == UTC


def test_future_timestamps_filtered():
    """Points after `now` are dropped (the store layer also rejects them)."""
    future_ms = int(_PINNED_NOW.timestamp() * 1000) + 3_600_000
    payload = _payload(
        [_record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, 1.0], [future_ms, 2.0]])]
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [ObservationRecord("DASH-abc", DataType.flow, _TS1, 1.0)]


def test_empty_valid_yields_no_records():
    payload = _payload([_record(_FLOW_UUID, "Water Flow", "cfs", [])])
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_null_and_malformed_values_skipped_without_poisoning():
    """A null/short pair is skipped; sibling valid points in the same channel survive."""
    payload = _payload(
        [
            _record(
                _FLOW_UUID,
                "Water Flow",
                "cfs",
                [[_TS1_MS, None], [_TS2_MS], [_TS2_MS, 2.0]],
            )
        ]
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [ObservationRecord("DASH-abc", DataType.flow, _TS2, 2.0)]


def test_nonfinite_value_skipped():
    payload = _payload([_record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, float("nan")]])])
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_bool_value_skipped():
    """JSON booleans must not store as 1.0/0.0 (float(True) is finite)."""
    payload = _payload([_record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, True]])])
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_unknown_channel_does_not_poison_known_channel():
    payload = _payload(
        [
            _record(_AIR_UUID, "Air Temperature", "°F", [[_TS1_MS, 72.0]]),
            _record(_LEVEL_UUID, "Water Level", "feet", [[_TS1_MS, 6.0]]),
        ]
    )
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [ObservationRecord("DASH-abc", DataType.gauge, _TS1, 6.0)]


def test_malformed_json_returns_empty():
    assert _new_parser().parse_records("{not json", now=_PINNED_NOW) == []


def test_missing_value_envelope_returns_empty():
    assert _new_parser().parse_records(json.dumps({"success": True}), now=_PINNED_NOW) == []


# ---------------------------------------------------------------------------
# parse() — DB-write path + error logging
# ---------------------------------------------------------------------------


def test_parse_logs_and_returns_zero_on_bad_json(caplog):
    with caplog.at_level("ERROR"):
        assert _new_parser().parse("{not json") == 0
    assert "JSON parse error" in caplog.text


def test_parse_stores_all_three_types(session, sample_source):
    """The DB-write path stores flow, gauge, and temperature under the lone source."""
    payload = _payload(
        [
            _record(_FLOW_UUID, "Water Flow", "cfs", [[_TS1_MS, 305.0]]),
            _record(_LEVEL_UUID, "Water Level", "feet", [[_TS1_MS, 6.85]]),
            _record(_TEMP_UUID, "Water Temperature", "°F", [[_TS1_MS, 57.3]]),
        ]
    )
    parser = LicorParser(
        url=_CONFIG_URL,
        session=session,
        source_id=sample_source.id,
        source_map={sample_source.name: sample_source.id},
    )
    # pin now via parse_records default is datetime.now; _TS1 (2023) is well in
    # the past so it survives the future guard without pinning here.
    count = parser.parse(payload)
    assert count == 3

    obs = session.scalars(
        select(Observation).where(Observation.source_id == sample_source.id)
    ).all()
    by_type = {o.data_type: o.value for o in obs}
    assert by_type == {
        DataType.flow: pytest.approx(305.0),
        DataType.gauge: pytest.approx(6.85),
        DataType.temperature: pytest.approx(57.3),
    }
    # single distinct station → no lone-source mis-attribution warning
    assert not parser.unknown_stations
