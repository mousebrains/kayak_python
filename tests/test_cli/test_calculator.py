"""Tests for the calculator CLI command and _safe_eval."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kayak.cli.calculator import _safe_eval, calculator
from kayak.db.data_db import get_latest, store_observation, update_latest, update_latest_gauge
from kayak.db.models import CalcExpression, DataType, Gauge, GaugeSource, Source


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
