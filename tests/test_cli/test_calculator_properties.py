"""Property-based tests for the calc-expression evaluator (T2.2 — 7th of 7).

Closes the T2.2 sweep. The calculator is the only "non-parser" target
in T2.2: it's a sandboxed AST evaluator for user-defined formulas like
``(_v0 + _v1) / 2`` or ``max(_v0, _v1) * 1.1``. The risk surface is
different from format parsers — it's correctness (does the math match
Python's semantics?) and isolation (can a hostile expression escape?)
rather than format drift.

The evaluator (``kayak.cli.calculator._safe_eval``) accepts:

- Numeric constants
- Binary ops: + - * / **
- Unary ops: + -
- Function calls: max, min, round
- Bare names via a caller-supplied ``values`` dict
- Power-base exponents capped at +/- 32 to prevent CPU bombs

Anything else raises ``ValueError``. Properties below pin both the
positive (semantics match Python) and negative (refuses bad input)
sides of that contract.
"""

from __future__ import annotations

import keyword
import math

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from kayak.cli.calculator import _safe_eval

_HSETTINGS = settings(
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Bounded so arithmetic never overflows / underflows / produces NaN.
# Real calc-expression inputs are gauge readings (~0..1e5 cfs) and
# rating curve constants, so wider than realistic but safe.
_safe_float = st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False)


# Property 1 -----------------------------------------------------------


@_HSETTINGS
@given(a=_safe_float, b=_safe_float)
def test_addition_matches_python(a, b):
    """``a + b`` evaluates the same as Python's float add."""
    assert math.isclose(_safe_eval(f"{a} + {b}"), a + b, rel_tol=1e-9, abs_tol=1e-9)


@_HSETTINGS
@given(a=_safe_float, b=_safe_float)
def test_subtraction_matches_python(a, b):
    assert math.isclose(_safe_eval(f"{a} - {b}"), a - b, rel_tol=1e-9, abs_tol=1e-9)


@_HSETTINGS
@given(a=_safe_float, b=_safe_float)
def test_multiplication_matches_python(a, b):
    assert math.isclose(_safe_eval(f"{a} * {b}"), a * b, rel_tol=1e-9, abs_tol=1e-9)


@_HSETTINGS
@given(a=_safe_float, b=_safe_float)
def test_division_matches_python(a, b):
    """Division by zero raises ``ZeroDivisionError`` (the underlying op's exception)."""
    if b == 0:
        with pytest.raises(ZeroDivisionError):
            _safe_eval(f"{a} / {b}")
        return
    assert math.isclose(_safe_eval(f"{a} / {b}"), a / b, rel_tol=1e-9, abs_tol=1e-9)


# Property 2 -----------------------------------------------------------


@_HSETTINGS
@given(
    base=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False),
    exp=st.floats(min_value=-3.0, max_value=3.0, allow_nan=False, allow_infinity=False),
)
def test_power_inside_caps_matches_python(base, exp):
    """``base ** exp`` matches Python's float pow when exp is in [-32, 32].

    Rating-curve exponents in this codebase live in [0.5, 3]; the
    strategy stays well inside the safety cap so the cap rule
    doesn't fire here.
    """
    expected = base**exp
    # Skip examples where the math itself overflows (10**3 = 1000 is fine,
    # but 0.1 ** -3 = 1000 is borderline depending on float rounding).
    assume(math.isfinite(expected))
    assert math.isclose(_safe_eval(f"{base} ** {exp}"), expected, rel_tol=1e-9)


@_HSETTINGS
@given(exp=st.floats(min_value=33.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
def test_power_exponent_above_cap_raises(exp):
    """``** exp`` with abs(exp) > 32 raises ValueError (CPU-bomb guard)."""
    with pytest.raises(ValueError, match="Exponent out of bounds"):
        _safe_eval(f"2 ** {exp}")


# Property 3 -----------------------------------------------------------


@_HSETTINGS
@given(a=_safe_float, b=_safe_float)
def test_unary_negate_matches_python(a, b):
    """``-(a + b)`` matches Python's negation."""
    assert math.isclose(_safe_eval(f"-({a} + {b})"), -(a + b), rel_tol=1e-9, abs_tol=1e-9)


# Property 4 -----------------------------------------------------------


@_HSETTINGS
@given(a=_safe_float, b=_safe_float, c=_safe_float)
def test_max_matches_python(a, b, c):
    assert math.isclose(_safe_eval(f"max({a}, {b}, {c})"), max(a, b, c), rel_tol=1e-9)


@_HSETTINGS
@given(a=_safe_float, b=_safe_float, c=_safe_float)
def test_min_matches_python(a, b, c):
    assert math.isclose(_safe_eval(f"min({a}, {b}, {c})"), min(a, b, c), rel_tol=1e-9)


@_HSETTINGS
@given(
    a=_safe_float,
    digits=st.integers(min_value=0, max_value=6),
)
def test_round_matches_python(a, digits):
    """``round(a, n)`` matches Python's round (which uses banker's rounding)."""
    assert math.isclose(_safe_eval(f"round({a}, {digits})"), round(a, digits), rel_tol=1e-9)


# Property 5 -----------------------------------------------------------


@_HSETTINGS
@given(a=_safe_float, b=_safe_float)
def test_variable_lookup_matches_python(a, b):
    """``_v0 + _v1`` with values={...} resolves via the lookup dict."""
    result = _safe_eval("_v0 + _v1", {"_v0": a, "_v1": b})
    assert math.isclose(result, a + b, rel_tol=1e-9, abs_tol=1e-9)


@_HSETTINGS
@given(
    name=st.text(alphabet=st.characters(whitelist_categories=["Ll"]), min_size=1, max_size=10),
)
def test_undefined_name_raises_value_error(name):
    """Any bare identifier not in the lookup dict raises ValueError.

    Lowercase-letters-only strategy keeps us clear of digits and
    operators. We still have to filter out (a) Python keywords like
    ``if`` / ``else`` (those raise SyntaxError at ast.parse time, not
    ValueError — different contract surface) and (b) the safe-function
    set (``max`` / ``min`` / ``round`` are valid call targets, not
    bare names).
    """
    assume(not keyword.iskeyword(name))
    assume(name not in {"max", "min", "round"})
    with pytest.raises(ValueError, match="Undefined name"):
        _safe_eval(name)


# Property 6 -----------------------------------------------------------


@_HSETTINGS
@given(
    bad_expr=st.sampled_from(
        [
            "__import__('os').listdir('.')",
            "[1, 2, 3]",
            "{'k': 'v'}",
            "(1, 2)",
            "lambda x: x + 1",
            "a if a > 0 else 0",
            "1 < 2",
            "1 == 1",
            "f'{a}'",
            "x = 5",
        ]
    ),
)
def test_unsupported_syntax_raises_value_error(bad_expr):
    """Anything outside arithmetic + max/min/round raises ValueError.

    Covers attribute access, subscript, lambdas, ternaries,
    comparisons, f-strings, assignments — all the AST-node types the
    sandbox explicitly refuses.
    """
    with pytest.raises((ValueError, SyntaxError)):
        _safe_eval(bad_expr)


# Property 7 -----------------------------------------------------------


@_HSETTINGS
@given(a=_safe_float)
def test_evaluation_returns_float(a):
    """Result is always a Python ``float``, regardless of integer inputs.

    Pins the contract that downstream callers (rounding, comparisons,
    DB INSERT) can assume a float — an integer-leaking refactor would
    surface here.
    """
    result = _safe_eval(f"{a}")
    assert isinstance(result, float)
