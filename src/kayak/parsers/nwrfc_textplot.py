"""NWRFC textPlot parser for observed inflow/discharge/stage data.

Endpoint: https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id={LID}&pe={PE}

Returns an HTML table whose left half is observed data and right half
is forecast. Only observed data is stored. The number of value columns
per side depends on the station and ``pe`` (Physical Element) query:

* ``pe=QI`` (inflow) / ``pe=QR`` (river discharge) — 1 value column.
* ``pe=HG`` on a gage-only station — 1 value column (Stage).
* ``pe=HG`` on a rated station — 2 value columns (Stage + Discharge),
  which we emit as gauge + flow for the same timestamp.
* ``pe=TW`` (water temperature) — 1 value column (Temperature), already
  in °F, which is the unit the rest of the pipeline stores.

The schema is inferred from the column-header row at the top of the
table. A page with *no* header row falls back to a 1-column flow/inflow
heuristic (covers truncated/error bodies and test fixtures). A page whose
header row *is* present but names a column we don't map — ``pe=HP``'s
"Pool Height", say — yields nothing at all: the label is a known-unknown,
and guessing "flow" there would republish a pool elevation or a
temperature as a discharge. Stale is recoverable; wrong is not.
"""

import logging
import re
from datetime import UTC, datetime

from kayak.db.models import DataType
from kayak.parsers.base import BaseParser, ObservationRecord
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)

_LABEL_TO_DTYPE = {
    "stage": DataType.gauge,
    "discharge": DataType.flow,
    "inflow": DataType.inflow,
    "temperature": DataType.temperature,
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

    def parse_records(
        self,
        text: str,
        *,
        now: datetime | None = None,
    ) -> list[ObservationRecord]:
        """Pure: HTML → records. No session, no DB.

        Returns ``[]`` for empty/malformed bodies — the regex-based
        extraction is tolerant by design (no exceptions to catch).
        """
        if now is None:
            now = datetime.now(UTC)

        station = self._extract_station(self.url)
        header_lower = text.lower()
        tz = "PDT" if "(pdt)" in header_lower else "PST" if "(pst)" in header_lower else None

        value_dtypes = self._infer_value_columns(text)
        if not value_dtypes:
            # Header named a column we don't map (already logged). Refuse the
            # page rather than let a zero-width row regex quietly match nothing.
            return []

        # Build the row regex: datetime + N value cells (one <td> each).
        value_re = r"\s*<td[^>]*>\s*([\d.]+)\s*</td>" * len(value_dtypes)
        pattern = (
            r"<tr>\s*"
            r"<td[^>]*>\s*([\d]{4}-[\d]{2}-[\d]{2}\s+[\d]{2}:[\d]{2})\s*</td>" + value_re
        )

        records: list[ObservationRecord] = []
        for m in re.finditer(pattern, text):
            when = parse_datetime(m.group(1).strip(), tz_name=tz)
            if when is None or when > now:
                continue
            for i, dt in enumerate(value_dtypes):
                val = safe_float(m.group(i + 2))
                if val is None or val < 0:
                    continue
                records.append(ObservationRecord(station, dt, when, val))
        return records

    @staticmethod
    def _infer_value_columns(text: str) -> list[DataType]:
        """Pick a DataType for each observed value column.

        Real NWRFC pages carry one header row like::

            <tr><td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td>
                <td>Date/Time (PDT)</td><td>Stage</td><td>Discharge</td></tr>

        The observed columns are everything up to the *second* Date/Time
        cell (which begins the forecast half).

        Returns ``[]`` when that header is present but names a column
        outside ``_LABEL_TO_DTYPE``. The heuristic below is only for
        bodies with *no* header — truncated, error pages, or the
        simplified shape used in unit tests — where a 1-column
        flow/inflow guess is the best available. Once a header has
        parsed, the page has told us its schema, and an unmapped label
        means we don't understand it: emitting nothing (a visibly stale
        gauge) beats relabelling the column as flow. Note this refusal is
        per-page and total, because the 1-column fallback would otherwise
        re-capture column 1 under the wrong type and corrupt an adjacent
        *known* column too.
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
            observed = [c.strip() for c in cells[:forecast_split]]
            dtypes: list[DataType] = []
            for c in observed:
                dt = _LABEL_TO_DTYPE.get(c.lower())
                if dt is None:
                    logger.error(
                        "unmapped textPlot column %r in header %r; storing nothing",
                        c,
                        observed,
                    )
                    return []
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
