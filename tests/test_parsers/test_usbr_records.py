"""Session-free tests for USBRParser.parse_records (T3.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from kayak.db.models import DataType
from kayak.parsers.base import ObservationRecord
from kayak.parsers.usbr import USBRParser
from kayak.utils.conversions import celsius_to_fahrenheit

_URL = "https://example.com/usbr"


def _new_parser(source_tz_map: dict[str, str] | None = None) -> USBRParser:
    """Construct without a session; parse_records doesn't use it.

    `source_tz_map` mirrors what `kayak.cli.fetch` builds from
    `source.timezone` per station, so the records test can exercise
    the same localization path that dump_to_db would use.
    """
    return USBRParser(
        url=_URL,
        session=None,  # type: ignore[arg-type]
        source_tz_map=source_tz_map,
    )


def test_parse_records_naive_timestamps_when_no_tz_map():
    """No source_tz_map → timestamps stay naive (matches dump_to_db)."""
    payload = "DateTime,stn1_q\n06/15/2026 12:00,1520.0\n"
    records = _new_parser().parse_records(payload)
    # parse_datetime(assume_naive=True) returns naive — no tz info.
    assert records == [
        ObservationRecord("STN1", DataType.flow, datetime(2026, 6, 15, 12, 0), 1520.0),
    ]


def test_parse_records_localizes_via_source_tz_map():
    """Per-station tz map → naive timestamp gets converted to UTC."""
    # USBR pn-hydromet publishes in local time. For an Oregon station that
    # means PDT in June (UTC-7) → 2026-06-15 12:00 local = 19:00 UTC.
    parser = _new_parser(source_tz_map={"STN1": "America/Los_Angeles"})
    payload = "DateTime,stn1_q\n06/15/2026 12:00,1520.0\n"
    records = parser.parse_records(payload)
    expected_when = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(
        UTC
    )
    assert records == [
        ObservationRecord("STN1", DataType.flow, expected_when, 1520.0),
    ]


def test_parse_records_celsius_converted_to_fahrenheit():
    """``wc`` code passes through celsius_to_fahrenheit before record emit."""
    payload = "DateTime,temp1_wc\n06/15/2026 12:00,20.0\n"
    records = _new_parser().parse_records(payload)
    assert records[0].data_type == DataType.temperature
    assert records[0].value == celsius_to_fahrenheit(20.0)


def test_parse_records_unknown_code_dropped():
    """Codes outside _CODE_MAP (e.g. ``zz``) emit no record."""
    payload = "DateTime,stn1_zz\n06/15/2026 12:00,1.0\n"
    assert _new_parser().parse_records(payload) == []


def test_parse_records_multi_station():
    """Multi-station CSV emits one record per non-null cell."""
    payload = (
        "DateTime,mado_gh,mado_q,waro_gh\n"
        "06/15/2026 12:00,3.48,196.00,3.32\n"
        "06/15/2026 13:00,3.47,194.00,3.31\n"
    )
    records = _new_parser().parse_records(payload)
    assert len(records) == 6  # 3 cells * 2 rows
    # Cells appear in declared header order per row.
    expected_stations = ["MADO", "MADO", "WARO"] * 2
    assert [r.station for r in records] == expected_stations
    assert [r.data_type for r in records] == [
        DataType.gauge,
        DataType.flow,
        DataType.gauge,
        DataType.gauge,
        DataType.flow,
        DataType.gauge,
    ]


def test_parse_records_html_wrapped_csv():
    """USBR sometimes wraps CSV in <PRE>; the strip layer runs first."""
    payload = "<HTML><BODY><PRE>\nDateTime,stn1_q\n06/15/2026 12:00,1520.0\n</PRE></BODY></HTML>"
    records = _new_parser().parse_records(payload)
    assert [r.value for r in records] == [1520.0]


def test_parse_records_empty_returns_empty():
    assert _new_parser().parse_records("") == []


def test_parse_records_idempotent_across_calls():
    """Re-running parse_records on a fresh instance must not carry header state."""
    parser = _new_parser()
    parser.parse_records("DateTime,stn1_q\n06/15/2026 12:00,1.0\n")
    # The second call's header should be re-parsed cleanly.
    records = parser.parse_records("DateTime,stn2_gh\n06/15/2026 13:00,9.0\n")
    assert records == [
        ObservationRecord("STN2", DataType.gauge, datetime(2026, 6, 15, 13, 0), 9.0),
    ]
