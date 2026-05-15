"""Session-free tests for NWRFCTextPlotParser.parse_records (T3.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from kayak.db.models import DataType
from kayak.parsers.base import ObservationRecord
from kayak.parsers.nwrfc_textplot import NWRFCTextPlotParser

_PINNED_NOW = datetime(2026, 6, 15, 14, 0, 0, tzinfo=UTC)
_URL = "https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=TESTW&pe=QR"


def _new_parser(url: str = _URL) -> NWRFCTextPlotParser:
    return NWRFCTextPlotParser(url=url, session=None)  # type: ignore[arg-type]


def test_parse_records_flow_simple_header():
    """1-column Discharge header: each row → one flow record."""
    payload = """\
<html><body><table>
<tr><td>Discharge</td><td>Forecast</td></tr>
<tr>
<td>2026-06-15 12:00</td>
<td>1520.0</td>
<td>2026-06-15 18:00</td>
<td>1600</td>
</tr>
</table></body></html>
"""
    # No (pdt)/(pst) header → tz=None → datetimes parse as naive → UTC.
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert records == [
        ObservationRecord("TESTW", DataType.flow, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), 1520.0),
    ]


def test_parse_records_inflow_keyword_fallback():
    """No header → '>inflow<' substring forces DataType.inflow."""
    payload = """\
<html><body><table>
<tr><td>Inflow</td><td>Forecast</td></tr>
<tr>
<td>2026-06-15 12:00</td>
<td>800.0</td>
<td></td><td></td>
</tr>
</table></body></html>
"""
    records = _new_parser().parse_records(payload, now=_PINNED_NOW)
    assert [r.data_type for r in records] == [DataType.inflow]
    assert records[0].value == 800.0


def test_parse_records_hg_stage_and_discharge_pairs():
    """pe=HG rated station: 2-column header yields paired gauge + flow."""
    payload = """\
<html><body><table>
<tr><td colspan="3" align="left">Observed</td><td colspan="3" align="left">Forecast/Trend</td></tr>
<tr><td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td>
    <td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td></tr>
<tr><td>2026-06-15 06:45</td><td>10.03</td><td>2807</td>
    <td>2026-06-15 17:00</td><td>10.01</td><td>2774</td></tr>
</table></body></html>
"""
    # PDT = UTC-7 → 2026-06-15 06:45 PDT = 13:45 UTC, < _PINNED_NOW (14:00 UTC).
    records = _new_parser(
        url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=EUGO3&pe=HG"
    ).parse_records(payload, now=_PINNED_NOW)
    when_pdt = datetime(2026, 6, 15, 6, 45, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert records == [
        ObservationRecord("EUGO3", DataType.gauge, when_pdt, 10.03),
        ObservationRecord("EUGO3", DataType.flow, when_pdt, 2807.0),
    ]


def test_parse_records_future_timestamps_dropped():
    payload = """\
<html><body><table>
<tr><td>Discharge</td></tr>
<tr><td>2099-01-01 00:00</td><td>9999.0</td></tr>
</table></body></html>
"""
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_negative_values_dropped():
    payload = """\
<html><body><table>
<tr><td>Discharge</td></tr>
<tr><td>2026-06-15 12:00</td><td>-100.0</td></tr>
</table></body></html>
"""
    assert _new_parser().parse_records(payload, now=_PINNED_NOW) == []


def test_parse_records_empty_or_garbage_returns_empty():
    parser = _new_parser()
    assert parser.parse_records("", now=_PINNED_NOW) == []
    assert parser.parse_records("<!doctype html><h1>502 Bad Gateway</h1>", now=_PINNED_NOW) == []
