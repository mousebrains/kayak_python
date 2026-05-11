"""Tests for the calculator CLI command and _safe_eval."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kayak.cli.calculator import _safe_eval, calculator
from kayak.db.cache import get_latest, update_latest, update_latest_gauge
from kayak.db.models import CalcExpression, DataType, Gauge, GaugeSource, Source
from kayak.db.observations import store_observation


def _noop(*a, **kw):
    pass


# ---------------------------------------------------------------------------
# _safe_eval unit tests
# ---------------------------------------------------------------------------


class TestSafeEval:
    def test_simple_arithmetic(self):
        assert _safe_eval("2 + 3") == 5.0

    def test_multiplication(self):
        assert _safe_eval("4 * 5") == 20.0

    def test_division(self):
        assert _safe_eval("10 / 4") == 2.5

    def test_power(self):
        assert _safe_eval("2 ** 3") == 8.0

    def test_power_within_bounds(self):
        # Real river-flow exponents (square root for stage-discharge curves).
        assert _safe_eval("100 ** 0.5") == 10.0
        assert _safe_eval("16 ** -0.5") == 0.25

    def test_power_exponent_out_of_bounds(self):
        with pytest.raises(ValueError, match="Exponent out of bounds"):
            _safe_eval("2 ** 100")
        with pytest.raises(ValueError, match="Exponent out of bounds"):
            _safe_eval("10 ** -100")

    def test_unary_neg(self):
        assert _safe_eval("-5 + 10") == 5.0

    def test_max_function(self):
        assert _safe_eval("max(1, 5, 3)") == 5.0

    def test_min_function(self):
        assert _safe_eval("min(10, 2, 7)") == 2.0

    def test_nested_expression(self):
        assert _safe_eval("max(0, 100.5 - 50.0 + 10.0)") == 60.5

    def test_rejects_import(self):
        with pytest.raises((ValueError, SyntaxError)):
            _safe_eval("__import__('os').system('ls')")

    def test_rejects_name_lookup(self):
        with pytest.raises(ValueError):
            _safe_eval("x + 1")

    def test_round_no_decimals(self):
        assert _safe_eval("round(3.7)") == 4.0

    def test_round_with_decimals(self):
        assert _safe_eval("round(3.14159, 2)") == 3.14

    def test_round_nested(self):
        assert _safe_eval("round(max(0, 0.25 * 1110.0))") == 278.0

    def test_rejects_unsupported_function(self):
        with pytest.raises(ValueError):
            _safe_eval("abs(-5)")

    def test_resolves_names_from_values(self):
        assert _safe_eval("_v0 + _v1", {"_v0": 10.0, "_v1": 5.0}) == 15.0

    def test_raises_on_missing_name(self):
        with pytest.raises(ValueError, match="Undefined name"):
            _safe_eval("_v0 + _v1", {"_v0": 10.0})


# ---------------------------------------------------------------------------
# calculator() integration tests
# ---------------------------------------------------------------------------


def _make_calc_source(session, expression, time_expression, ref_sources):
    """Create a source with a calc_expression and reference sources with latest values.

    Returns source_id.
    """
    calc_expr = CalcExpression(
        data_type=DataType.flow,
        expression=expression,
        time_expression=time_expression,
    )
    session.add(calc_expr)
    session.flush()

    source = Source(name="calc_test_source", agency="CALC", calc_expression_id=calc_expr.id)
    session.add(source)
    session.flush()
    source_id = source.id

    # Create a gauge for the calc source and link it
    calc_gauge = Gauge(name="calc_test_gauge")
    session.add(calc_gauge)
    session.flush()
    session.add(GaugeSource(gauge_id=calc_gauge.id, source_id=source_id))

    for name, dtype, value in ref_sources:
        ref_src = Source(name=name, agency="TEST")
        session.add(ref_src)
        session.flush()

        # Create a gauge with the same name as the source and link them
        ref_gauge = Gauge(name=name)
        session.add(ref_gauge)
        session.flush()
        session.add(GaugeSource(gauge_id=ref_gauge.id, source_id=ref_src.id))

        now = datetime.now(UTC) - timedelta(hours=1)
        store_observation(session, ref_src.id, dtype, now, value)
        update_latest(session, ref_src.id, dtype)
        update_latest_gauge(session, ref_gauge.id, dtype)

    session.flush()
    return source_id


def _run_calculator(session):
    """Run calculator with session.close/commit patched to no-ops."""
    with (
        patch("kayak.cli.calculator.get_session", return_value=session),
        patch.object(session, "close", _noop),
        patch.object(session, "commit", _noop),
    ):
        calculator(SimpleNamespace())


def test_simple_expression(session):
    """Calculator evaluates a simple arithmetic expression."""
    ref_sources = [("src_a", DataType.flow, 100.0), ("src_b", DataType.flow, 50.0)]
    expression = "src_a::flow + src_b::flow"
    time_expression = "src_a::flow src_b::flow"

    source_id = _make_calc_source(session, expression, time_expression, ref_sources)
    _run_calculator(session)

    latest = get_latest(session, source_id, DataType.flow)
    assert latest is not None
    assert latest.value == 150.0


def test_greatest_converted_to_max(session):
    """greatest() in expression is converted to max() for evaluation."""
    ref_sources = [("src_a", DataType.flow, 100.0), ("src_b", DataType.flow, 200.0)]
    expression = "greatest(src_a::flow, src_b::flow)"
    time_expression = "src_a::flow src_b::flow"

    source_id = _make_calc_source(session, expression, time_expression, ref_sources)
    _run_calculator(session)

    latest = get_latest(session, source_id, DataType.flow)
    assert latest is not None
    assert latest.value == 200.0


def test_missing_reference_skipped(session):
    """If a referenced source has no latest value, calculation is skipped."""
    calc_expr = CalcExpression(
        data_type=DataType.flow,
        expression="missing_src::flow + 10",
        time_expression="missing_src::flow",
    )
    session.add(calc_expr)
    session.flush()

    source = Source(name="calc_missing_ref", agency="CALC", calc_expression_id=calc_expr.id)
    session.add(source)
    session.flush()
    source_id = source.id

    # Create the referenced source but don't add any observations
    ref_src = Source(name="missing_src", agency="TEST")
    session.add(ref_src)
    session.flush()

    _run_calculator(session)

    latest = get_latest(session, source_id, DataType.flow)
    assert latest is None


def test_negative_result_clamped_to_zero(session):
    """Negative results are clamped to 0."""
    ref_sources = [("src_a", DataType.flow, 10.0), ("src_b", DataType.flow, 50.0)]
    expression = "src_a::flow - src_b::flow"
    time_expression = "src_a::flow src_b::flow"

    source_id = _make_calc_source(session, expression, time_expression, ref_sources)
    _run_calculator(session)

    latest = get_latest(session, source_id, DataType.flow)
    assert latest is not None
    assert latest.value == 0.0


def test_substring_refs_do_not_collide(session):
    """When one ref name is a substring of another, both resolve correctly.

    Regression test: the old string-replace would substitute the shorter ref
    inside the longer one. E.g. with refs "x::flow" and "x::flowrate", the
    pattern "x::flow" matches inside "x::flowrate". Longest-first substitution
    via placeholders prevents this.
    """
    # Names chosen so one is a substring of the other. Observation values
    # differ so we can prove both got resolved correctly.
    ref_sources = [
        ("gauge_X", DataType.flow, 7.0),
        ("gauge_X_tributary", DataType.flow, 100.0),
    ]
    expression = "gauge_X_tributary::flow + gauge_X::flow"
    time_expression = "gauge_X_tributary::flow gauge_X::flow"

    source_id = _make_calc_source(session, expression, time_expression, ref_sources)
    _run_calculator(session)

    latest = get_latest(session, source_id, DataType.flow)
    assert latest is not None
    # Correct answer: 100 + 7 = 107. If the substring bug were present, the
    # shorter ref "gauge_X::flow" would substitute inside "gauge_X_tributary::flow"
    # and produce a syntactically garbled expression.
    assert latest.value == 107.0


def test_non_finite_reference_is_skipped(session):
    """If any referenced value is non-finite (nan/inf), skip the calculation."""
    ref_sources = [("src_a", DataType.flow, float("inf"))]
    expression = "src_a::flow * 2"
    time_expression = "src_a::flow"

    source_id = _make_calc_source(session, expression, time_expression, ref_sources)
    _run_calculator(session)

    latest = get_latest(session, source_id, DataType.flow)
    assert latest is None


def test_circular_dependency_raises(session):
    """A cycle among calc sources fails the pipeline step instead of being silently reordered."""
    # time_expression format is "agency::ref_name::datatype" — three tokens
    # joined by double colons (see _get_deps regex in calculator.py).
    calc_a_expr = CalcExpression(
        data_type=DataType.flow,
        expression="v::calc_b::flow + 1",
        time_expression="v::calc_b::flow",
    )
    calc_b_expr = CalcExpression(
        data_type=DataType.flow,
        expression="v::calc_a::flow + 1",
        time_expression="v::calc_a::flow",
    )
    session.add_all([calc_a_expr, calc_b_expr])
    session.flush()

    src_a = Source(name="calc_a", agency="CALC", calc_expression_id=calc_a_expr.id)
    src_b = Source(name="calc_b", agency="CALC", calc_expression_id=calc_b_expr.id)
    session.add_all([src_a, src_b])
    session.flush()

    gauge_a = Gauge(name="calc_a")
    gauge_b = Gauge(name="calc_b")
    session.add_all([gauge_a, gauge_b])
    session.flush()
    session.add_all(
        [
            GaugeSource(gauge_id=gauge_a.id, source_id=src_a.id),
            GaugeSource(gauge_id=gauge_b.id, source_id=src_b.id),
        ]
    )
    session.flush()

    with pytest.raises(ValueError, match="Circular dependency"):
        _run_calculator(session)


def test_calc_reads_other_datatype_from_own_gauge(session):
    """A calc that reads a different data type from its own gauge isn't a cycle.

    Regression for Fall_Creek_above_Winberry_calc: it produces 'flow' on the
    Fall_Creek_Inflow gauge while reading that gauge's 'inflow' (supplied by a
    different, fetched source on the same gauge). The topo sort must not flag
    this as a self-cycle.
    """
    # A calc producing 'flow' that consumes 'inflow' from its own gauge.
    calc_expr = CalcExpression(
        data_type=DataType.flow,
        expression="v::shared_gauge::inflow - x::tributary::flow",
        time_expression="v::shared_gauge::inflow x::tributary::flow",
    )
    session.add(calc_expr)
    session.flush()

    calc_src = Source(name="self_ref_calc", agency="CALC", calc_expression_id=calc_expr.id)
    session.add(calc_src)
    session.flush()

    # The calc's own gauge — also has a fetched inflow source on it.
    shared_gauge = Gauge(name="shared_gauge")
    session.add(shared_gauge)
    session.flush()
    session.add(GaugeSource(gauge_id=shared_gauge.id, source_id=calc_src.id))

    inflow_src = Source(name="inflow_fetch", agency="TEST")
    session.add(inflow_src)
    session.flush()
    session.add(GaugeSource(gauge_id=shared_gauge.id, source_id=inflow_src.id))

    trib_src = Source(name="tributary_fetch", agency="TEST")
    trib_gauge = Gauge(name="tributary")
    session.add_all([trib_src, trib_gauge])
    session.flush()
    session.add(GaugeSource(gauge_id=trib_gauge.id, source_id=trib_src.id))

    now = datetime.now(UTC) - timedelta(hours=1)
    store_observation(session, inflow_src.id, DataType.inflow, now, 650.0)
    update_latest(session, inflow_src.id, DataType.inflow)
    update_latest_gauge(session, shared_gauge.id, DataType.inflow)
    store_observation(session, trib_src.id, DataType.flow, now, 195.0)
    update_latest(session, trib_src.id, DataType.flow)
    update_latest_gauge(session, trib_gauge.id, DataType.flow)
    session.flush()

    _run_calculator(session)  # must not raise

    latest = get_latest(session, calc_src.id, DataType.flow)
    assert latest is not None
    assert latest.value == 455.0  # 650 - 195
