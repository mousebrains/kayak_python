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

# pe=HG response from a rated NWRFC station: observed columns carry both
# Stage (ft) and Discharge (cfs), forecast half mirrors that layout.
# Captured live from EUGO3 (WILLAMETTE--AT EUGENE) on 2026-05-11.
TEXTPLOT_HG_STAGE_DISCHARGE = """\
<html><body><table>
<tr><td colspan="3" align="left">Observed</td><td colspan="3" align="left">Forecast/Trend</td></tr>
<tr><td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td>
    <td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td></tr>
<tr><td>2024-06-15 15:45</td><td>10.03</td><td>2807</td>
    <td>2024-06-15 17:00</td><td>10.01</td><td>2774</td></tr>
<tr><td>2024-06-15 15:30</td><td>10.04</td><td>2824</td>
    <td>2024-06-15 23:00</td><td>10.01</td><td>2774</td></tr>
</table></body></html>
"""

# pe=TW response: water temperature (°F), observed-only — the page carries
# no forecast half, so the header row has a single Date/Time cell and the
# data rows trail empty spacer cells.
# Captured live from LAPO3 (LITTLE DESCHUTES--NR LAPINE) on 2026-07-15.
TEXTPLOT_TW_TEMPERATURE = """\
<html><body>
LITTLE DESCHUTES--NR LAPINE (LAPO3)<br><br><table border="0" cellspacing="5">
<tr><td colspan="2" align="left">Observed</td></tr>
<tr><td>Date/Time (PDT)</td><td align="right">Temperature</td></tr>
<tr><td align="right">2024-06-15 08:15</td><td align="right">69.4</td><td>&nbsp;</td><td>&nbsp;</td></tr>
<tr><td align="right">2024-06-15 08:00</td><td align="right">69.6</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</table></body></html>
"""

# pe=HP: reservoir pool elevation, a column we deliberately do not map.
# Shape captured live from DETO3 (Detroit Lake) on 2026-07-15, where the
# real page serves ~1031 rows of pool height in feet.
TEXTPLOT_HP_POOL_HEIGHT = """\
<html><body><table>
<tr><td colspan="2" align="left">Observed</td></tr>
<tr><td>Date/Time (PDT)</td><td align="right">Pool Height</td></tr>
<tr><td align="right">2024-06-15 08:15</td><td align="right">1543.71</td></tr>
<tr><td align="right">2024-06-15 08:00</td><td align="right">1543.702</td></tr>
</table></body></html>
"""

# A known column (Stage) sitting next to an unmapped one. The 1-column
# fallback used to re-capture Stage as flow, corrupting a column we do
# understand — so an unmapped label has to void the whole page.
TEXTPLOT_MIXED_KNOWN_AND_UNKNOWN = """\
<html><body><table>
<tr><td>Date/Time (PDT)</td><td>Stage</td><td>Pool Height</td>
    <td>Date/Time (PDT)</td><td>Stage</td><td>Pool Height</td></tr>
<tr><td>2024-06-15 15:45</td><td>9.73</td><td>1543.71</td>
    <td>2024-06-15 17:00</td><td>9.71</td><td>1543.70</td></tr>
</table></body></html>
"""

# pe=HG on a gage-only NWRFC station: only Stage appears in the observed
# half. Captured live from OCUO3 (Willamette Upper Falls) on 2026-05-11.
TEXTPLOT_HG_STAGE_ONLY = """\
<html><body><table>
<tr><td>Date/Time (PDT)</td><td>Stage</td>
    <td>Date/Time (PDT)</td><td>Stage</td></tr>
<tr><td>2024-06-15 15:30</td><td>54.08</td>
    <td>2024-06-15 17:00</td><td>54.06</td></tr>
<tr><td>2024-06-15 15:15</td><td>54.08</td>
    <td>2024-06-15 23:00</td><td>54.06</td></tr>
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


class TestNWRFCTextPlotHG:
    def test_hg_stage_and_discharge_emits_both(self, session):
        """pe=HG on a rated station yields paired gauge + flow values."""
        src = _make_source(session, name="hg_pair")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=EUGO3&pe=HG",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_HG_STAGE_DISCHARGE)
        assert count == 4  # 2 rows * (gauge + flow)
        obs = session.query(Observation).filter_by(source_id=src.id).all()
        gauge_vals = sorted(o.value for o in obs if o.data_type == DataType.gauge)
        flow_vals = sorted(o.value for o in obs if o.data_type == DataType.flow)
        assert gauge_vals == [10.03, 10.04]
        assert flow_vals == [2807.0, 2824.0]
        # Sanity: every row contributed both data types at the same timestamp.
        gauge_times = {o.observed_at for o in obs if o.data_type == DataType.gauge}
        flow_times = {o.observed_at for o in obs if o.data_type == DataType.flow}
        assert gauge_times == flow_times

    def test_hg_stage_only_emits_gauge(self, session):
        """pe=HG on a gage-only station yields gauge values, not flow."""
        src = _make_source(session, name="hg_stage_only")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=OCUO3&pe=HG",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_HG_STAGE_ONLY)
        assert count == 2
        obs = session.query(Observation).filter_by(source_id=src.id).all()
        assert all(o.data_type == DataType.gauge for o in obs)
        assert sorted(o.value for o in obs) == [54.08, 54.08]


class TestNWRFCTextPlotTW:
    def test_tw_emits_temperature(self, session):
        """pe=TW yields temperature values in °F.

        Regression guard: before ``Temperature`` was in ``_LABEL_TO_DTYPE``
        the header lookup failed, the column-inference bailed to its
        1-column ``flow`` fallback, and a 69.4 °F reading was stored as
        69.4 cfs — silently publishing a temperature as a discharge.
        """
        src = _make_source(session, name="tw_temperature")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=LAPO3&pe=TW",
            session=session,
            source_id=src.id,
        )
        count = parser.parse(TEXTPLOT_TW_TEMPERATURE)
        assert count == 2
        obs = session.query(Observation).filter_by(source_id=src.id).all()
        assert all(o.data_type == DataType.temperature for o in obs)
        assert sorted(o.value for o in obs) == [69.4, 69.6]

    def test_tw_header_infers_temperature_column(self):
        """The observed half of a pe=TW page is a single Temperature column."""
        parser = NWRFCTextPlotParser.__new__(NWRFCTextPlotParser)
        assert parser._infer_value_columns(TEXTPLOT_TW_TEMPERATURE) == [DataType.temperature]


class TestNWRFCTextPlotUnmappedColumn:
    """A parsed header naming a column we don't map must store nothing.

    The old code fell through to a 1-column ``flow`` guess, which is how a
    69.4 °F reading would have become 69.4 cfs. The same fallback still
    reaches ``pe=HP``, which NWRFC serves today: Detroit Lake's pool
    elevation of 1543.71 ft would post as 1543.71 cfs.
    """

    def test_pool_height_stores_nothing(self, session):
        src = _make_source(session, name="hp_pool")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=DETO3&pe=HP",
            session=session,
            source_id=src.id,
        )
        assert parser.parse(TEXTPLOT_HP_POOL_HEIGHT) == 0
        assert session.query(Observation).filter_by(source_id=src.id).all() == []

    def test_unmapped_column_does_not_corrupt_a_known_neighbour(self, session):
        """Stage must not be re-emitted as flow just because a sibling is unknown."""
        src = _make_source(session, name="hp_mixed")
        parser = NWRFCTextPlotParser(
            url="https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=DETO3&pe=HG",
            session=session,
            source_id=src.id,
        )
        assert parser.parse(TEXTPLOT_MIXED_KNOWN_AND_UNKNOWN) == 0
        obs = session.query(Observation).filter_by(source_id=src.id).all()
        assert obs == []
        assert not any(o.value == 9.73 for o in obs), "stage leaked as another data type"

    def test_infer_returns_empty_rather_than_guessing_flow(self):
        parser = NWRFCTextPlotParser.__new__(NWRFCTextPlotParser)
        assert parser._infer_value_columns(TEXTPLOT_HP_POOL_HEIGHT) == []
        assert parser._infer_value_columns(TEXTPLOT_MIXED_KNOWN_AND_UNKNOWN) == []

    def test_headerless_bodies_still_use_the_heuristic(self):
        """The fallback survives for the case it was actually written for."""
        parser = NWRFCTextPlotParser.__new__(NWRFCTextPlotParser)
        assert parser._infer_value_columns(TEXTPLOT_FLOW) == [DataType.flow]
        assert parser._infer_value_columns(TEXTPLOT_INFLOW) == [DataType.inflow]
