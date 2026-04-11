"""NWRFC textPlot parser for observed inflow/discharge data.

Endpoint: https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id={LID}&pe={PE}

Returns an HTML table with observed data in columns 1-2 (datetime, value)
and forecast data in columns 3-4. Only observed data is stored.

The data type (flow vs inflow) is determined from the column header
("Discharge" -> flow, "Inflow" -> inflow).
"""

import logging
import re
from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@register("nwrfc.textplot")
class NWRFCTextPlotParser(BaseParser):
    """NW River Forecast Center HTML table parser.

    Parses observed-data HTML tables from the NWRFC textPlot endpoint.
    Extracts flow (cfs/kcfs) or gage height (ft) from table rows. Uses
    regex to match table cells rather than line-by-line processing.
    """

    name = "nwrfc.textplot"

    def parse(self, text: str) -> int:
        """Parse HTML table from NWRFC textPlot endpoint."""
        self._db_updates = 0
        self._obs_buffer = []

        station = self._extract_station(self.url)

        # Determine data type from header row
        data_type = DataType.flow
        header_lower = text.lower()
        if ">inflow<" in header_lower:
            data_type = DataType.inflow

        now = datetime.now(UTC)

        # Extract table rows: each <tr> has 4 <td> cells
        # Columns 1-2 are observed (datetime, value)
        for m in re.finditer(
            r"<tr>\s*"
            r"<td[^>]*>\s*([\d]{4}-[\d]{2}-[\d]{2}\s+[\d]{2}:[\d]{2})\s*</td>\s*"
            r"<td[^>]*>\s*([\d.]+)\s*</td>",
            text,
        ):
            time_str = m.group(1).strip()
            val_str = m.group(2).strip()

            when = parse_datetime(time_str)
            if when is None or when > now:
                continue

            val = safe_float(val_str)
            if val is None or val < 0:
                continue

            self.dump_to_db(station, data_type, when, val)

        self._flush_buffer()

        if self._db_updates == 0:
            logger.warning("No database updates from %s parser(%s)", self.url, self.name)

        return self._db_updates

    def parse_line(self, line: str) -> bool:
        return True

    @staticmethod
    def _extract_station(url: str) -> str:
        """Extract station LID from textPlot URL query string."""
        m = re.search(r"[?&]id=([^&]+)", url)
        return m.group(1) if m else ""
