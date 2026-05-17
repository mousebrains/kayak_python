"""Session-free tests for PacifiCorpParser.parse_records."""

from __future__ import annotations

from datetime import datetime

from kayak.db.models import DataType
from kayak.parsers.base import ObservationRecord
from kayak.parsers.pacificorp import PacifiCorpParser

_URL = "https://www.pacificorp.com/etc/pcorp/datafiles/hydro/RogueRiverBypass.xml"
_STATION = "PR2R.NFD_BYP_80FL_PI"


def _new_parser() -> PacifiCorpParser:
    return PacifiCorpParser(url=_URL, session=None)  # type: ignore[arg-type]


def _wrap(station: str, unit: str, values_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Measurements>
    <Measurement>
        <PowerSystemResourceId>{station}</PowerSystemResourceId>
        <MeasurementUnit>{unit}</MeasurementUnit>
{values_xml}
    </Measurement>
</Measurements>
"""


def _good_value(ts: str, value: str) -> str:
    return f"""        <MeasurementValue>
            <timeStamp>{ts}</timeStamp>
            <value>{value}</value>
            <MeasurementValueQuality>
                <validity>0</validity>
            </MeasurementValueQuality>
        </MeasurementValue>"""


def test_parse_records_two_good_rows_csf_unit():
    payload = _wrap(
        _STATION,
        "csf",
        _good_value("2026-05-11 01:59:59", "88")
        + "\n"
        + _good_value("2026-05-11 02:59:59", "89"),
    )
    records = _new_parser().parse_records(payload)
    assert records == [
        ObservationRecord(_STATION, DataType.flow, datetime(2026, 5, 11, 1, 59, 59), 88.0),
        ObservationRecord(_STATION, DataType.flow, datetime(2026, 5, 11, 2, 59, 59), 89.0),
    ]


def test_parse_records_corrected_cfs_unit_also_accepted():
    payload = _wrap(_STATION, "cfs", _good_value("2026-05-11 01:59:59", "100"))
    records = _new_parser().parse_records(payload)
    assert records == [
        ObservationRecord(_STATION, DataType.flow, datetime(2026, 5, 11, 1, 59, 59), 100.0),
    ]


def test_parse_records_skips_bad_validity_and_na_value():
    bad_value = """        <MeasurementValue>
            <timeStamp>2026-05-17 08:59:59</timeStamp>
            <value>n/a</value>
            <MeasurementValueQuality>
                <validity>-248</validity>
            </MeasurementValueQuality>
        </MeasurementValue>"""
    payload = _wrap(
        _STATION,
        "csf",
        _good_value("2026-05-11 01:59:59", "88") + "\n" + bad_value,
    )
    records = _new_parser().parse_records(payload)
    assert records == [
        ObservationRecord(_STATION, DataType.flow, datetime(2026, 5, 11, 1, 59, 59), 88.0),
    ]


def test_parse_records_skips_negative_flow():
    payload = _wrap(_STATION, "csf", _good_value("2026-05-11 01:59:59", "-5"))
    assert _new_parser().parse_records(payload) == []


def test_parse_records_skips_empty_value_and_timestamp():
    payload = _wrap(
        _STATION,
        "csf",
        _good_value("2026-05-11 01:59:59", "")
        + "\n"
        + _good_value("", "88"),
    )
    assert _new_parser().parse_records(payload) == []


def test_parse_records_handles_multiple_measurement_blocks():
    payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<Measurements>
    <Measurement>
        <PowerSystemResourceId>STN_A</PowerSystemResourceId>
        <MeasurementUnit>csf</MeasurementUnit>
{_good_value("2026-05-11 01:59:59", "88")}
    </Measurement>
    <Measurement>
        <PowerSystemResourceId>STN_B</PowerSystemResourceId>
        <MeasurementUnit>cfs</MeasurementUnit>
{_good_value("2026-05-11 02:59:59", "200")}
    </Measurement>
</Measurements>
"""
    records = _new_parser().parse_records(payload)
    assert records == [
        ObservationRecord("STN_A", DataType.flow, datetime(2026, 5, 11, 1, 59, 59), 88.0),
        ObservationRecord("STN_B", DataType.flow, datetime(2026, 5, 11, 2, 59, 59), 200.0),
    ]


def test_parse_records_skips_unknown_units():
    payload = _wrap(_STATION, "feet", _good_value("2026-05-11 01:59:59", "3.45"))
    assert _new_parser().parse_records(payload) == []


def test_parse_records_malformed_xml_returns_empty():
    assert _new_parser().parse_records("<Measurements><not closed") == []


def test_parse_records_empty_string_returns_empty():
    assert _new_parser().parse_records("") == []
