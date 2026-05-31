"""Session-free tests for WaGovParser.parse_records (T3.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from kayak.db.models import DataType
from kayak.parsers.base import ObservationRecord
from kayak.parsers.wa_gov import WaGovParser
from kayak.utils.conversions import celsius_to_fahrenheit


def _new_parser(source_tz_map: dict[str, str] | None = None) -> WaGovParser:
    """parse_records doesn't touch session/source_id — pass a sentinel."""
    return WaGovParser(
        url="https://example.com/wa",
        session=None,  # type: ignore[arg-type]
        source_tz_map=source_tz_map,
    )


def test_parse_records_temperature_celsius_to_fahrenheit():
    """``Water_Temp`` header → DataType.temperature with C→F conversion."""
    payload = (
        "STATION1--Test Station Description\n"
        "DATE TIME Water_Temp  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  18.5  100\n"
    )
    records = _new_parser().parse_records(payload)
    assert records == [
        ObservationRecord(
            "STATION1",
            DataType.temperature,
            datetime(2026, 6, 15, 12, 0),
            celsius_to_fahrenheit(18.5),
        ),
    ]


def test_parse_records_stage_gauge():
    """``Stage`` header → DataType.gauge, no value conversion."""
    payload = (
        "STATION2--Another Station\n"
        "DATE TIME Stage  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  3.45  100\n"
    )
    records = _new_parser().parse_records(payload)
    assert records == [
        ObservationRecord("STATION2", DataType.gauge, datetime(2026, 6, 15, 12, 0), 3.45),
    ]


def test_parse_records_localizes_via_source_tz_map():
    """`Etc/GMT+8` is a fixed UTC-8 offset (PST year-round)."""
    parser = _new_parser(source_tz_map={"STATION3": "Etc/GMT+8"})
    payload = (
        "STATION3--PST Station\n"
        "DATE TIME Stage  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  3.45  100\n"
    )
    records = parser.parse_records(payload)
    expected = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("Etc/GMT+8")).astimezone(UTC)
    assert records == [
        ObservationRecord("STATION3", DataType.gauge, expected, 3.45),
    ]


def test_parse_records_quality_filter_rejects_zero_and_200():
    """Quality must be 1-199 inclusive; 0 and 200+ skip."""
    payload = (
        "STATION4--Bad Quality Station\n"
        "DATE TIME Water_Temp  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  18.5  0\n"
        "06/15/2026 13:00  19.0  200\n"
        "06/15/2026 14:00  20.0  100\n"
    )
    records = _new_parser().parse_records(payload)
    assert len(records) == 1
    assert records[0].observed_at == datetime(2026, 6, 15, 14, 0)


def test_parse_records_no_data_lines_skipped():
    """`No Data` value rows are dropped (typical mid-feed gap)."""
    payload = (
        "STATION5--Holes In Feed\n"
        "DATE TIME Water_Temp  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  No Data  100\n"
        "06/15/2026 13:00  18.5  100\n"
    )
    records = _new_parser().parse_records(payload)
    assert len(records) == 1
    assert records[0].observed_at == datetime(2026, 6, 15, 13, 0)


def test_parse_records_multi_station_resets_state():
    """A bare `Quality` line between station blocks flips state back to 0.

    The header-detection filter requires ≥2 tokens on the station-id row;
    the live WA DOE format meets that with the ``--description`` suffix.
    """
    payload = (
        "STATIONA--Block One\n"
        "DATE TIME Stage  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  1.0  100\n"
        "Quality 0\n"
        "STATIONB--Block Two\n"
        "DATE TIME Stage  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 13:00  2.0  100\n"
    )
    records = _new_parser().parse_records(payload)
    assert [r.station for r in records] == ["STATIONA", "STATIONB"]
    assert [r.value for r in records] == [1.0, 2.0]


def test_parse_records_empty_or_garbage_returns_empty():
    parser = _new_parser()
    assert parser.parse_records("") == []
    assert parser.parse_records("<!doctype html><h1>502 Bad Gateway</h1>") == []
    assert (
        parser.parse_records(
            "Some Header Line\ngarbage,without,enough,fields\n,,,,\n*** TRUNCATED ***\n"
        )
        == []
    )


def test_parse_records_single_tz_fallback_on_renamed_source():
    """wa.gov rename case: the source is named by file stem (29C100_STG_FM), so
    source_tz_map keys on the stem — but the parser still reads the bare 29C100
    from the file header. With exactly one source tz on the fetch, _localize
    applies it (BaseParser single-tz fallback)."""
    parser = _new_parser(source_tz_map={"29C100_STG_FM": "Etc/GMT+8"})
    payload = (
        "29C100--Renamed PST Station\n"
        "DATE TIME Stage  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  3.45  100\n"
    )
    records = parser.parse_records(payload)
    expected = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("Etc/GMT+8")).astimezone(UTC)
    assert records == [
        ObservationRecord("29C100", DataType.gauge, expected, 3.45),
    ]


def test_parse_records_no_tz_fallback_when_multiple_sources():
    """Multi-station feed (>1 source tz): an unmapped station gets NO fallback —
    the single-tz shortcut is guarded so per-station tz never leaks across."""
    parser = _new_parser(source_tz_map={"A_STG": "Etc/GMT+8", "B_STG": "America/Boise"})
    payload = (
        "29C100--Unmapped Station\n"
        "DATE TIME Stage  Quality\n"
        "---  ---  -------  -------\n"
        "06/15/2026 12:00  3.45  100\n"
    )
    records = parser.parse_records(payload)
    assert records == [
        ObservationRecord("29C100", DataType.gauge, datetime(2026, 6, 15, 12, 0), 3.45),
    ]
