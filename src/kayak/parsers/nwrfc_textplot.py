"""NWRFC textPlot parser for observed inflow/discharge/stage data.

Endpoint: https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id={LID}&pe={PE}

Returns an HTML table whose left half is observed data and right half
is forecast. Only observed data is stored. The number of value columns
per side depends on the station and ``pe`` (Physical Element) query:

* ``pe=QI`` (inflow) / ``pe=QR`` (river discharge) — 1 value column.
* ``pe=HG`` on a gage-only station — 1 value column (Stage).
* ``pe=HG`` on a rated station — 2 value columns (Stage + Discharge),
  which we emit as gauge + flow for the same timestamp.

The schema is inferred from the column-header row at the top of the
table; pages without a recognisable header fall back to a 1-column
flow/inflow heuristic (covers truncated/error bodies and test fixtures).
"""

import logging
import re
from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


_LABEL_TO_DTYPE = {
    "stage": DataType.gauge,
    "discharge": DataType.flow,
    "inflow": DataType.inflow,
}


@register("nwrfc.textplot")
class NWRFCTextPlotParser(BaseParser):
    """NW River Forecast Center HTML table parser.

    Parses observed-data HTML tables from the NWRFC textPlot endpoint.
    Reads the column-header row to decide which DataType each value cell
    represents, then walks each data row capturing the leading observed
    columns. Forecast columns sit later in the row and aren't anchored
    to ``<tr>``, so they're naturally skipped.
    """

    name = "nwrfc.textplot"

    def parse(self, text: str) -> int:
        self._db_updates = 0
        self._obs_buffer = []

        station = self._extract_station(self.url)
        header_lower = text.lower()

        tz = "PDT" if "(pdt)" in header_lower else "PST" if "(pst)" in header_lower else None
        now = datetime.now(UTC)

        value_dtypes = self._infer_value_columns(text)

        # Build the row regex: datetime + N value cells (one <td> each).
        value_re = r"\s*<td[^>]*>\s*([\d.]+)\s*</td>" * len(value_dtypes)
        pattern = (
            r"<tr>\s*"
            r"<td[^>]*>\s*([\d]{4}-[\d]{2}-[\d]{2}\s+[\d]{2}:[\d]{2})\s*</td>" + value_re
        )

        for m in re.finditer(pattern, text):
            when = parse_datetime(m.group(1).strip(), tz_name=tz)
            if when is None or when > now:
                continue
            for i, dt in enumerate(value_dtypes):
                val = safe_float(m.group(i + 2))
                if val is None or val < 0:
                    continue
                self.dump_to_db(station, dt, when, val)

        self._flush_buffer()

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def parse_line(self, line: str) -> bool:
        return True

    @staticmethod
    def _infer_value_columns(text: str) -> list[DataType]:
        """Pick a DataType for each observed value column.

        Real NWRFC pages carry one header row like::

            <tr><td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td>
                <td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td></tr>

        The observed columns are everything up to the *second* Date/Time
        cell (which begins the forecast half). If no such header is
        present — truncated body, error page, or the simplified shape
        used in unit tests — fall back to a 1-column schema and infer
        flow vs. inflow from the surrounding text.
        """
        m = re.search(
            r"<tr>\s*<td[^>]*>\s*Date/Time[^<]*</td>"
            r"((?:\s*<td[^>]*>[^<]*</td>)+)\s*</tr>",
            text,
            re.IGNORECASE,
        )
        if m is not None:
            cells = re.findall(r"<td[^>]*>([^<]*)</td>", m.group(1))
            forecast_split = next(
                (i for i, c in enumerate(cells) if "date/time" in c.lower()),
                len(cells),
            )
            dtypes: list[DataType] = []
            for c in cells[:forecast_split]:
                dt = _LABEL_TO_DTYPE.get(c.strip().lower())
                if dt is None:
                    dtypes = []
                    break
                dtypes.append(dt)
            if dtypes:
                return dtypes

        if ">inflow<" in text.lower():
            return [DataType.inflow]
        return [DataType.flow]

    @staticmethod
    def _extract_station(url: str) -> str:
        """Extract station LID from textPlot URL query string."""
        m = re.search(r"[?&]id=([^&]+)", url)
        return m.group(1) if m else ""
