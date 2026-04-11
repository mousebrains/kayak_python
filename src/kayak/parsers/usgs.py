"""USGS RDB tab-delimited parser (replaces Parse_USGS.C).

Parses USGS waterdata RDB (tab-delimited) files with a 3-state machine:
  State 0: Header line (agency_cd, site_no, datetime, tz_cd, ...)
  State 1: Width line (skipped)
  State 2: Data rows

Each column like ``01_00060`` encodes a USGS parameter code that maps to
a data type (flow, gage, temperature, etc.) with optional unit conversion.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from kayak.parsers.base import BaseParser
from kayak.parsers.registry import register
from kayak.utils.conversions import parse_datetime, safe_float

logger = logging.getLogger(__name__)


@dataclass
class _Parameter:
    """USGS parameter code mapping to database type + conversion."""

    code: int
    key: str
    db_key: str = ""
    min_val: float = -1e30
    slope: float = 1.0
    intercept: float = 0.0
    rounding: float = 0.0

    def valid(self, raw: float) -> bool:
        return bool(self.db_key) and raw > self.min_val

    def convert(self, raw: float) -> float:
        val = raw * self.slope + self.intercept
        if self.rounding:
            val = round(val / self.rounding) * self.rounding
        return val


def _build_parameters() -> dict[int, _Parameter]:
    """Build the USGS parameter code lookup table (from initParameters())."""
    p = _Parameter
    params = [
        # Temperature parameters
        p(10, "temperatureCelsius", "temperature", slope=1.8, intercept=32, rounding=0.1),
        p(11, "temperature", "temperature"),
        # Flow parameters
        p(60, "flow", "flow", min_val=0),
        p(61, "flow", "flow", min_val=0),
        # Gage parameters
        p(65, "gage", "gauge", min_val=-999),
        # Everything else — no db_key means we skip it
        p(3, "samplingDepth"),
        p(9, "gotMe"),
        p(20, "airTemperatureCelsius"),
        p(21, "airTemperature"),
        p(25, "airPressure"),
        p(30, "solarRadiation"),
        p(35, "windSpeed"),
        p(36, "windDirection"),
        p(42, "altitude"),
        p(45, "precipitation"),
        p(47, "partialPressuremm"),
        p(48, "gotMe"),
        p(52, "relativeHumidity"),
        p(53, "reservoirSurfaceArea"),
        p(54, "reservoirStorage"),
        p(55, "streamVelocity"),
        p(59, "streamFlowRate"),
        p(62, "reservoirElevation"),
        p(63, "numberSamplingPoints"),
        p(64, "streamDepth"),
        p(67, "tideStage"),
        p(70, "turbidityJackson"),
        p(72, "gageMeters"),
        p(76, "turbidity"),
        p(90, "oxidationReductionPotential"),
        p(95, "specificConductance"),
        p(96, "salinity"),
        p(200, "gotMe"),
        p(300, "disolvedOxygen"),
        p(301, "disolvedOxygenSaturation"),
        p(400, "ph"),
        p(480, "SalinityPPT"),
        p(931, "SodiumAdsorptionRatio"),
        p(30208, "flowCubicMetersPerSecond"),
        p(30209, "flowCubicMetersPerSecond"),
        p(32209, "chlorophyllAFlurometric"),
        p(32210, "chlorophyllATrichromatic"),
        p(32234, "chlorophyllTotalSpectrophotometric"),
        p(45587, "gotMe"),
        p(46515, "solarRadiationDownLangley"),
        p(46529, "precipitation"),
        p(50011, "temperatureVentGas"),
        p(50042, "flowGPM"),
        p(50294, "gotMe"),
        p(61028, "turbidityUnfiltered"),
        p(61035, "gotMe"),
        p(61727, "windGustSpeed"),
        p(61728, "gotMe"),
        p(61729, "windGustDirection"),
        p(62361, "chlorophyllTotalFluorometric"),
        p(62608, "solarRadiationDownWattsPerSquareMeter"),
        p(62614, "elevation"),
        p(62615, "gotMe"),
        p(62625, "gotMe"),
        p(63158, "gotMe"),
        p(63680, "gotMe"),
        p(70969, "batteryVoltage"),
        p(72001, "holeDepth"),
        p(72019, "depth"),
        p(72020, "reservoirElevation"),
        p(72111, "DCPtransmissionErrorCodes"),
        p(72112, "DCPsignal2noiseRatio"),
        p(72113, "DCPsignalModulationIndex"),
        p(72114, "DCPtransmittedPower"),
        p(72115, "DCPfrequencyDrift"),
        p(72116, "DCPbadCharacters"),
        p(72117, "DCPtransmissionDelay"),
        p(72124, "gotMe"),
        p(72126, "gotMe"),
        p(72137, "gotMe"),
        p(72147, "gotMe"),
        p(74207, "soilMoisturePercentVolume"),
        p(75969, "airPressureUncorrected"),
        p(80154, "sedimentmgperlitre"),
        p(80155, "sedimenttonperday"),
        p(81026, "waterContentSnowInches"),
        p(81903, "gotMe"),
        p(82127, "airSpeedKnots"),
        p(82292, "DRGSsourceNodeCode"),
        p(82300, "snowDepthInches"),
        p(90856, "gotMe"),
        p(90860, "gotMe"),
        p(91055, "gotMe"),
        p(95202, "gotMe"),
        p(99060, "dischargeCubicMetersPerSecond"),
        p(99065, "gageMeters"),
        p(99900, "other"),
        p(99901, "other"),
        p(99902, "gotMe"),
        p(99903, "gotMe"),
        p(99904, "gotMe"),
        p(99905, "gotMe"),
        p(99906, "gotMe"),
        p(99968, "gotMe"),
        p(99971, "gotMe"),
    ]
    return {param.code: param for param in params}


_PARAMETERS = _build_parameters()


@register("usgs")
class USGSParser(BaseParser):
    """USGS RDB tab-delimited data parser."""

    name = "usgs"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._state = 0
        self._columns: list[str] = []
        self._fields: dict[str, int] = {}  # column_name -> index

    def parse_line(self, line: str) -> bool:
        if not line or line.startswith("#"):
            self._state = 0
            return True

        tokens = line.split("\t")

        if self._state == 0:
            # Header line: agency_cd  site_no  datetime  tz_cd  ...
            if tokens and tokens[0] == "agency_cd":
                self._columns = tokens
                self._fields = {col: i for i, col in enumerate(tokens)}
                self._state = 1
            return True

        if self._state == 1:
            # Width line — skip but validate column count
            if len(tokens) != len(self._columns):
                logger.error(
                    "Column count mismatch: %d names vs %d widths",
                    len(self._columns),
                    len(tokens),
                )
                self._state = 0
            else:
                self._state = 2
            return True

        if self._state == 2:
            # Data row
            return self._parse_data_row(tokens)

        logger.error("Unsupported state %d for %s", self._state, self.url)
        return True

    def _get_field(self, tokens: list[str], field: str) -> str:
        """Get a field value from a token list by column name."""
        idx = self._fields.get(field)
        if idx is None or idx >= len(tokens):
            return ""
        return tokens[idx]

    def _parse_data_row(self, tokens: list[str]) -> bool:
        station = self._get_field(tokens, "site_no")
        tz = self._get_field(tokens, "tz_cd")
        time_str = self._get_field(tokens, "datetime")

        if not station:
            logger.error("No station in line")
            return True

        dt = parse_datetime(time_str, tz)
        if dt is None:
            logger.error("Cannot parse time '%s' tz '%s'", time_str, tz)
            return True

        for i, col_name in enumerate(self._columns):
            if not col_name or col_name.endswith("_cd"):
                continue
            if col_name in ("site_no", "datetime", "agency_cd", "tz_cd"):
                continue

            # Column format: NN_PPPPP where NN is sequence, PPPPP is param code
            parts = col_name.split("_")
            if len(parts) != 2:
                continue

            try:
                param_code = int(parts[1])
            except ValueError:
                continue

            param = _PARAMETERS.get(param_code)
            if param is None or not param.db_key:
                continue

            if i >= len(tokens):
                continue

            raw = safe_float(tokens[i])
            if raw is None:
                continue

            val = param.convert(raw)
            if not param.valid(raw) or math.isinf(val):
                continue

            self.dump_to_db(station, param.db_key, dt, val)

        return True
