"""Property-based tests for the wa.gov (WA DOE) parser (T2.2 — fifth parser).

WA DOE serves a fixed-width text format with a three-state machine:
state 0 captures the ``STATION--description`` header, state 1 waits for
the dashed separator under the column headers, state 2+ reads data
rows. Each data row has DATE TIME VALUE QUALITY columns and the parser
filters on a quality-code band (``1 ≤ q < 200``).

Like USBR, the parser does Celsius → Fahrenheit conversion (rounded to
0.1 °F) for the ``Water_Temp`` data-type header, and doesn't filter
future timestamps — wa.gov is observed-only and timestamps are PST
year-round (localized via source.timezone at dump_to_db time).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kayak.db.models import DataType, Observation, Source
from kayak.parsers.wa_gov import WaGovParser

_HSETTINGS = settings(
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_STATION = "STN1"
_VALID_QUALITY = st.integers(min_value=1, max_value=199)
_INVALID_QUALITY = st.one_of(
    st.integers(min_value=0, max_value=0),
    st.integers(min_value=200, max_value=999),
    st.integers(min_value=-100, max_value=-1),
)


def _recent_wa_date(hours_back: int = 1) -> str:
    """wa.gov uses MM/DD/YYYY HH:MM (zero-padded, naive UTC for tests)."""
    when = datetime.now(UTC) - timedelta(hours=hours_back)
    return when.replace(microsecond=0).strftime("%m/%d/%Y %H:%M")


def _make_payload(
    *,
    column_label: str = "Stage",
    value: str | float = 1.0,
    quality: int = 100,
) -> str:
    """Build a wa.gov-shaped fixed-width text body with one station + one data row.

    Three lines after the station header constitute the per-column-set
    block: header row, dashed separator, then data rows until the next
    section. The parser's state machine resets when it sees a new
    station-header line, but a single station + a single data row is
    enough for property invariants.
    """
    return (
        f"{_STATION}--Test wa.gov Station\n"
        f"DATE TIME {column_label}  Quality\n"
        "---  ---  -------  -------\n"
        f"{_recent_wa_date()}  {value}  {quality}\n"
    )


def _new_parser(session, sample_source: Source) -> WaGovParser:
    return WaGovParser(
        url="test://wa-gov/data.txt",
        session=session,
        source_id=sample_source.id,
        # The parser's source_map shape: {station_name: source_id}. Bind
        # our test station so dump_to_db doesn't fall into the auto-
        # create-orphan path (which would mint a fresh Source row).
        source_map={_STATION: sample_source.id},
    )


# Property 1 -----------------------------------------------------------


@_HSETTINGS
@given(
    column_label=st.sampled_from(["Stage", "Water_Temp", "Flow", "Other"]),
    value=st.one_of(
        st.floats(min_value=-100, max_value=1e5, allow_nan=False, allow_infinity=False).map(str),
        st.sampled_from(["No Data", "", "n/a", "—"]),
    ),
    quality=st.integers(min_value=-100, max_value=999),
)
def test_parse_never_raises_on_well_formed_input(
    session, sample_source, column_label, value, quality
):
    """Parser handles any column label x value x quality without raising.

    Covers all four data-type branches plus the edge values that
    ``safe_float`` returns None for. A future refactor that drops the
    ``No Data`` short-circuit (line 80 in wa_gov.py) would surface here.
    """
    payload = _make_payload(column_label=column_label, value=value, quality=quality)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)


# Property 2 -----------------------------------------------------------


@_HSETTINGS
@given(quality=_INVALID_QUALITY)
def test_invalid_quality_never_stored(session, sample_source, quality):
    """``quality <= 0`` or ``quality >= 200`` always drops the row.

    Pin the wa.gov quality-code contract: 0 = "no quality info" (suspect),
    200+ = explicitly flagged bad. The parser refuses both — a future
    refactor that changes the band must update this strategy.
    """
    payload = _make_payload(column_label="Stage", value=3.45, quality=quality)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"quality={quality} produced {len(obs)} observations (should be dropped)"


# Property 3 -----------------------------------------------------------


@_HSETTINGS
@given(quality=_VALID_QUALITY)
def test_no_data_marker_never_stored(session, sample_source, quality):
    """The literal ``No Data`` substring in a row drops the whole row.

    Crossed against valid quality codes so the only reason to skip is
    the No-Data marker (property 2 covers the quality-filter path).
    """
    payload = _make_payload(column_label="Stage", value="No Data", quality=quality)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"'No Data' input with quality={quality} produced {len(obs)} observations"


# Property 4 -----------------------------------------------------------


@_HSETTINGS
@given(
    celsius=st.floats(min_value=-20.0, max_value=50.0, allow_nan=False, allow_infinity=False),
)
def test_water_temp_celsius_converted_to_fahrenheit(session, sample_source, celsius):
    """``Water_Temp`` header triggers Celsius → Fahrenheit conversion (rounded 0.1°F).

    Same lossy conversion as USBR's ``_wc`` code — the property pins
    the rounding contract.
    """
    # Hypothesis runs ~50 examples per test; clear prior rows so
    # .scalar() reflects this example's insert alone. Same pattern as
    # the nwps kcfs / usbr wc tests.
    session.query(Observation).filter_by(
        source_id=sample_source.id, data_type=DataType.temperature
    ).delete()
    session.flush()

    payload = _make_payload(column_label="Water_Temp", value=celsius, quality=100)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    stored = (
        session.query(Observation.value)
        .filter_by(source_id=sample_source.id, data_type=DataType.temperature)
        .scalar()
    )
    assert stored is not None
    expected = round(celsius * 1.8 + 32.0, 1)
    assert math.isclose(stored, expected, rel_tol=1e-9, abs_tol=1e-9), (
        f"Water_Temp={celsius}°C stored as {stored}°F, expected {expected}°F"
    )


# Property 5 -----------------------------------------------------------


@_HSETTINGS
@given(
    stage=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_stage_header_maps_to_gauge_data_type(session, sample_source, stage):
    """The ``Stage`` header line steers data into ``DataType.gauge``, not flow."""
    session.query(Observation).filter_by(
        source_id=sample_source.id, data_type=DataType.gauge
    ).delete()
    session.flush()

    payload = _make_payload(column_label="Stage", value=stage, quality=100)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    stored = (
        session.query(Observation.value)
        .filter_by(source_id=sample_source.id, data_type=DataType.gauge)
        .scalar()
    )
    assert stored is not None
    assert math.isclose(stored, stage, rel_tol=1e-9, abs_tol=1e-9), (
        f"Stage={stage} stored as {stored}"
    )
