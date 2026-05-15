"""Property-based tests for the NWPS parser (T2.2 — first parser).

Hypothesis-driven invariants the parser must hold across the input
space, complementing the example-based tests in ``test_nwps.py``. Per
the audit (TEST-G in PLAN_pre_release_followup.md), property tests
catch edge cases that hand-curated fixtures miss — particularly around
unit handling, sentinel-value filtering, and timestamp bounds.

Settings:

- ``derandomize=True`` — same Hypothesis seed across runs; a regression
  is reproducible, no CI flakes.
- ``database=None`` — don't persist the example database across runs.
  CI containers are ephemeral so the cache would never warm anyway.
- ``deadline=None`` — DB-touching tests can be slow on CI; bounded
  example counts (``max_examples`` per test) keep total runtime down.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kayak.db.models import DataType, Observation, Source
from kayak.parsers.nwps import NWPSParser

# Reusable settings: deterministic, CI-friendly. `function_scoped_fixture`
# is suppressed because the `session` fixture intentionally resets per
# example (each call gets a fresh transactional savepoint via conftest).
_HSETTINGS = settings(
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_SENTINELS = (-999, -9999)


def _recent_timestamp(hours_back: int = 1) -> str:
    """ISO-8601 ``Z`` timestamp ``hours_back`` hours before now."""
    when = datetime.now(UTC) - timedelta(hours=hours_back)
    return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future_timestamp(hours_ahead: int = 1) -> str:
    when = datetime.now(UTC) + timedelta(hours=hours_ahead)
    return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_payload(
    *,
    primary_units: str = "ft",
    secondary_units: str = "cfs",
    entries: list[dict] | None = None,
) -> str:
    return json.dumps(
        {
            "primaryUnits": primary_units,
            "secondaryUnits": secondary_units,
            "data": entries or [],
        }
    )


def _new_parser(session, sample_source: Source) -> NWPSParser:
    return NWPSParser(
        url="test://gauges/HLID/stageflow/observed",
        session=session,
        source_id=sample_source.id,
    )


# Strategies -----------------------------------------------------------

# Bounded floats — wider than realistic but inside parser's safe range
# (no infinities / NaNs; the parser filters those but generating them
# here would just exercise the same skip path).
_finite_floats = st.floats(
    min_value=-1e7,
    max_value=1e7,
    allow_nan=False,
    allow_infinity=False,
)

# Units the parser actually recognizes (it lowercases input), plus a
# scattering of unknowns to verify the "ignore unrecognized units" path.
_stage_units = st.sampled_from(["ft", "FT", "feet", "", "meters", "m"])
_flow_units = st.sampled_from(["cfs", "CFS", "kcfs", "KCFS", "", "m3/s"])


# Property 1 -----------------------------------------------------------


@_HSETTINGS
@given(
    primary_units=_stage_units,
    secondary_units=_flow_units,
    primary=st.one_of(st.none(), _finite_floats),
    secondary=st.one_of(st.none(), _finite_floats),
)
def test_parse_never_raises_on_well_formed_inputs(
    session, sample_source, primary_units, secondary_units, primary, secondary
):
    """The parser handles any valid combination of units/values without raising.

    Catches a broad class of input-shape regressions — a future refactor
    that assumes ``primaryUnits`` is non-empty, or that ``primary``
    keys exist, would crash here long before it crashed in prod.
    """
    payload = _make_payload(
        primary_units=primary_units,
        secondary_units=secondary_units,
        entries=[{"validTime": _recent_timestamp(), "primary": primary, "secondary": secondary}],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)  # no exception → test passes


# Property 2 -----------------------------------------------------------


@_HSETTINGS
@given(
    primary=st.sampled_from(_SENTINELS),
    secondary=st.sampled_from(_SENTINELS),
)
def test_sentinel_values_never_stored(session, sample_source, primary, secondary):
    """``-999`` / ``-9999`` in either field must not yield an Observation.

    Both sentinel slots filter independently. Earlier bug in this
    codebase had only ``primary`` filtered, so a sentinel ``secondary``
    flowed through as a real (negative) flow value.
    """
    payload = _make_payload(
        entries=[{"validTime": _recent_timestamp(), "primary": primary, "secondary": secondary}],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], (
        f"Sentinel input primary={primary}, secondary={secondary} produced {len(obs)} observations"
    )


# Property 3 -----------------------------------------------------------


@_HSETTINGS
@given(secondary=st.floats(min_value=-1e6, max_value=-1e-6, allow_nan=False, allow_infinity=False))
def test_negative_flow_never_stored(session, sample_source, secondary):
    """Strictly negative flow values are unphysical and must be dropped.

    Constrains the strategy to skip the ``-999/-9999`` sentinels (those
    are tested by property 2) and zero (zero is a legitimate flow).
    """
    if secondary in _SENTINELS:
        return
    payload = _make_payload(
        entries=[{"validTime": _recent_timestamp(), "primary": 5.0, "secondary": secondary}],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    flows = (
        session.query(Observation)
        .filter_by(source_id=sample_source.id, data_type=DataType.flow)
        .all()
    )
    assert flows == [], f"Negative flow {secondary} produced {len(flows)} flow observations"


# Property 4 -----------------------------------------------------------


@_HSETTINGS
@given(
    flow=st.floats(min_value=0.001, max_value=1000.0, allow_nan=False, allow_infinity=False),
)
def test_kcfs_input_is_converted_to_cfs(session, sample_source, flow):
    """``secondaryUnits=kcfs`` → stored value is ``input * 1000`` (within float tolerance).

    Bounds chosen to stay clear of the sentinel range and zero (which
    are tested separately). 1000 kcfs = 1 million cfs is well above
    real-world values but inside the strategy's safe arithmetic range.
    """
    # Hypothesis runs ~50 examples per test invocation, all sharing the
    # function-scoped session + sample_source. Without explicit
    # per-example cleanup the parser's flow inserts accumulate; on a
    # fast runner all 50 land within one second so ON CONFLICT
    # (source_id, data_type, observed_at) dedupes them into one row —
    # but on slower CI (e.g. 3.13) ``observed_at`` drifts across
    # seconds, distinct rows accumulate, and the .scalar() below
    # raises MultipleResultsFound. Clear flows from this source up-front
    # so the assertion measures this example's insert alone.
    session.query(Observation).filter_by(
        source_id=sample_source.id, data_type=DataType.flow
    ).delete()
    session.flush()

    payload = _make_payload(
        secondary_units="kcfs",
        entries=[{"validTime": _recent_timestamp(), "primary": 5.0, "secondary": flow}],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    stored = (
        session.query(Observation.value)
        .filter_by(source_id=sample_source.id, data_type=DataType.flow)
        .scalar()
    )
    assert stored is not None
    # math.isclose tolerates the float-mul roundoff that ``kcfs_to_cfs``
    # introduces. Relative tolerance of 1e-9 is wider than the actual
    # error (~1e-15) but cheap insurance against future re-implementation.
    assert math.isclose(stored, flow * 1000.0, rel_tol=1e-9), (
        f"kcfs input {flow} stored as {stored}, expected {flow * 1000.0}"
    )


# Property 5 -----------------------------------------------------------


@_HSETTINGS
@given(
    hours_ahead=st.integers(min_value=1, max_value=24 * 30),  # up to a month ahead
)
def test_future_timestamps_never_stored(session, sample_source, hours_ahead):
    """``validTime > now`` means a forecast or clock-skew; we drop those rows.

    The NWPS endpoint exposes both observed and forecast halves in
    other URL flavors; this parser is the ``/observed`` variant and
    should refuse anything that's not actually in the past.
    """
    payload = _make_payload(
        entries=[
            {"validTime": _future_timestamp(hours_ahead), "primary": 5.0, "secondary": 500.0},
        ],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Future input (+{hours_ahead}h) produced {len(obs)} observations"
