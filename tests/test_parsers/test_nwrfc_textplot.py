"""Tests for the NWRFC textPlot HTML parser."""

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.nwrfc_textplot import NWRFCTextPlotParser

TEXTPLOT_FLOW = """\
<html><body>
<table>
<tr><td>Discharge</td><td>Forecast</td></tr>
<tr>
<td>2024-06-15 12:00</td>
<td>1520.0</td>
<td>2024-06-16 00:00</td>
<td>1600</td>
</tr>
<tr>
<td>2024-06-15 13:00</td>
<td>1540.5</td>
<td>2024-06-16 01:00</td>
<td>1650</td>
</tr>
</table>
</body></html>
"""

TEXTPLOT_INFLOW = """\
<html><body>
<table>
<tr><td>Inflow</td><td>Forecast</td></tr>
<tr>
<td>2024-06-15 12:00</td>
<td>800.0</td>
<td></td><td></td>
</tr>
</table>
</body></html>
"""

TEXTPLOT_EMPTY = ""

TEXTPLOT_FUTURE = """\
<html><body><table>
<tr><td>Discharge</td></tr>
<tr>
<td>2099-01-01 00:00</td>
<td>9999.0</td>
</tr>
</table></body></html>
"""

TEXTPLOT_NEGATIVE = """\
<html><body><table>
<tr><td>Discharge</td></tr>
<tr>
<td>2024-06-15 12:00</td>
<td>-100.0</td>
</tr>
</table></body></html>
"""


def _make_source(session, name="nwrfc_textplot_test"):
    fu = FetchUrl(
        url=f"https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id={name}&pe=QR",
        parser="nwrfc.textplot",
        is_active=True,
    )
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestNWRFCTextPlotBasic:
    def test_parse_flow_data(self, session):
        src = _make_source(session)
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=TESTW&pe=QR",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_FLOW)
        assert count == 2
        obs = session.query(Observation).filter_by(source_id=src.id).all()
        assert len(obs) == 2
        assert all(o.data_type == DataType.flow for o in obs)

    def test_parse_inflow_data(self, session):
        src = _make_source(session, name="inflow_test")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=INFW&pe=QI",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_INFLOW)
        assert count == 1
        obs = session.query(Observation).filter_by(source_id=src.id).all()
        assert obs[0].data_type == DataType.inflow

    def test_extract_station_from_url(self):
        assert (
            NWRFCTextPlotParser._extract_station(
                "https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=TESTW&pe=QR"
            )
            == "TESTW"
        )

    def test_extract_station_missing(self):
        assert NWRFCTextPlotParser._extract_station("https://example.com/noparams") == ""


class TestNWRFCTextPlotEdgeCases:
    def test_empty_input(self, session):
        src = _make_source(session, name="empty_test")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=EMPTY&pe=QR",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_EMPTY)
        assert count == 0

    def test_future_timestamps_rejected(self, session):
        src = _make_source(session, name="future_test")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=FUTW&pe=QR",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_FUTURE)
        assert count == 0

    def test_negative_values_rejected(self, session):
        src = _make_source(session, name="neg_test")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=NEGW&pe=QR",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_NEGATIVE)
        assert count == 0

    def test_html_error_page(self, session):
        """Cloudflare-style HTML 502 page must not crash the text parser."""
        src = _make_source(session, name="html_err")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=ERRW&pe=QR",
            session=session,
            source_id=src.id,
        )
        html = "<!doctype html><html><body><h1>502 Bad Gateway</h1></body></html>"
        assert parser.parse(html) == 0

    def test_truncated_body(self, session):
        """A body truncated mid-row must not crash the parser."""
        src = _make_source(session, name="trunc")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=CUTW&pe=QR",
            session=session,
            source_id=src.id,
        )
        truncated = (
            "TEXT PLOT FOR CUTW (Cut River)\n"
            "Date/Time             Stage (ft)  Flow (cfs)\n"
            "2024-06-15 12:00         3.45     "
        )
        assert parser.parse(truncated) == 0
