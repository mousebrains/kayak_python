"""Property-based tests for the USBR Hydromet CSV parser (T2.2 — fourth parser).

USBR's CSV format has its own quirks: the station identifier is the
prefix of each column header (``mado_gh``, ``romo_q``), the data-type
code is the suffix mapped via ``_CODE_MAP`` (``q``→flow, ``gh``→gauge,
``wc``→Celsius-temp, ``wf``→Fahrenheit-temp), and the parser strips an
optional HTML wrapper (``<HTML><BODY><PRE>…</PRE></BODY></HTML>``) that
the Hydromet web service sometimes emits before serving the CSV.

Note: unlike nwps / usace.cda / nwrfc.xml, the USBR parser does *not*
filter future timestamps — Hydromet only publishes observed data and
the parser trusts the feed. No future-timestamp invariant tested here.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kayak.db.models import DataType, Observation, Source
from kayak.parsers.usbr import USBRParser

_HSETTINGS = settings(
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Codes _CODE_MAP recognizes (suffix after the station prefix).
_KNOWN_CODES = ("q", "qd", "qi", "qj", "qr", "qu", "gh", "hp", "ht", "fb", "wc", "wf", "ws")
# Codes outside _CODE_MAP — should be silently dropped during header parse.
_UNKNOWN_CODES = ("xy", "abc", "_xyz", "test", "")


def _recent_csv_date(hours_back: int = 1) -> str:
    """USBR uses MM/DD/YYYY HH:MM (zero-padded) in its CSV header."""
    when = datetime.now(UTC) - timedelta(hours=hours_back)
    return when.replace(microsecond=0).strftime("%m/%d/%Y %H:%M")


def _make_csv(*, header_col: str, value: str | float) -> str:
    """Two-row CSV: ``DateTime,<header_col>\n<recent>,<value>\n``."""
    return f"DateTime,{header_col}\n{_recent_csv_date()},{value}\n"


def _new_parser(session, sample_source: Source) -> USBRParser:
    return USBRParser(
        url="test://usbr/hydromet?format=csv",
        session=session,
        source_id=sample_source.id,
    )


# Property 1 -----------------------------------------------------------


@_HSETTINGS
@given(
    code=st.sampled_from((*_KNOWN_CODES, *_UNKNOWN_CODES)),
    # Realistic data-value range plus some pathological inputs the
    # parser must tolerate via safe_float — empty / non-numeric cells.
    value_str=st.one_of(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False).map(str),
        st.sampled_from(["", "  ", "n/a", "—", "missing"]),
    ),
)
def test_parse_never_raises_on_well_formed_csv(session, sample_source, code, value_str):
    """Parser handles any code x value combination without raising.

    Common regression vector here is a refactor of the header parser
    (``_parse_header``) that assumes every column matches ``\\w+_\\w+``;
    Hypothesis will hit the underscore-handling edge cases that hand
    fixtures miss.
    """
    payload = _make_csv(header_col=f"STN_{code}", value=value_str)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)  # no exception → test passes


# Property 2 -----------------------------------------------------------


@_HSETTINGS
@given(code=st.sampled_from(_UNKNOWN_CODES))
def test_unknown_code_never_stored(session, sample_source, code):
    """A header column with a suffix outside ``_CODE_MAP`` is dropped at parse time.

    Catches column-name regressions where a renamed station code silently
    starts being ignored (the parser logs at debug but won't trip CI
    without an assertion like this one).
    """
    if not code:
        # Empty-suffix columns ("STN_") parse as a station with empty
        # code; that's a separate edge case covered by property 1's
        # crash-resistance check. Skip here to keep the assertion clean.
        return
    payload = _make_csv(header_col=f"STN_{code}", value=100.0)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Unknown code STN_{code!r} produced {len(obs)} observations"


# Property 3 -----------------------------------------------------------


@_HSETTINGS
@given(value=st.sampled_from(["", "  ", "n/a", "ice", "missing"]))
def test_non_numeric_value_never_stored(session, sample_source, value):
    """A non-numeric data cell (empty, NaN sentinel, text) is dropped.

    ``safe_float`` returns ``None`` for these inputs; the parser must
    skip the row rather than crash on a NoneType arithmetic op
    downstream. The code-suffix here is held to a known mapping so
    only the value's parse-failure path is exercised.
    """
    payload = _make_csv(header_col="STN_q", value=value)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = (
        session.query(Observation)
        .filter_by(source_id=sample_source.id, data_type=DataType.flow)
        .all()
    )
    assert obs == [], f"Non-numeric value {value!r} produced {len(obs)} flow observations"


# Property 4 -----------------------------------------------------------


@_HSETTINGS
@given(
    celsius=st.floats(min_value=-20.0, max_value=50.0, allow_nan=False, allow_infinity=False),
)
def test_celsius_wc_converted_to_fahrenheit(session, sample_source, celsius):
    """``_wc`` columns are Celsius and must be stored as Fahrenheit.

    ``_wf`` and ``_ws`` are already in Fahrenheit and stored verbatim;
    only ``wc`` triggers the conversion. Range is realistic water-temp
    (-20…50 °C) so we don't drift into pathological floating-point
    territory.
    """
    # Hypothesis re-runs the same source across ~50 examples; clear
    # prior temperature rows so the .scalar() below isolates THIS
    # example's insert. Same accumulation guard as in the nwps kcfs
    # test (33e4998).
    session.query(Observation).filter_by(
        source_id=sample_source.id, data_type=DataType.temperature
    ).delete()
    session.flush()

    payload = _make_csv(header_col="STN_wc", value=celsius)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    stored = (
        session.query(Observation.value)
        .filter_by(source_id=sample_source.id, data_type=DataType.temperature)
        .scalar()
    )
    assert stored is not None
    # ``celsius_to_fahrenheit`` rounds to one decimal (1.25 °C → 34.2 °F
    # via banker's rounding, not 34.25 °F). Property test exists in
    # part to pin that contract — a future refactor that drops the
    # round() would surface here.
    expected = round(celsius * 1.8 + 32.0, 1)
    assert math.isclose(stored, expected, rel_tol=1e-9, abs_tol=1e-9), (
        f"wc={celsius}°C stored as {stored}°F, expected {expected}°F (rounded to 0.1°F)"
    )


# Property 5 -----------------------------------------------------------


@_HSETTINGS
@given(
    flow=st.floats(min_value=0.001, max_value=1e5, allow_nan=False, allow_infinity=False),
)
def test_html_wrapped_csv_is_parsed(session, sample_source, flow):
    """Hydromet sometimes wraps CSV in ``<HTML><BODY><PRE>…</PRE>…``.

    The parser's ``parse()`` override strips the wrapper before the
    base-class line iteration. This property pins the contract so a
    future cleanup that drops the strip step would surface here.
    """
    session.query(Observation).filter_by(
        source_id=sample_source.id, data_type=DataType.flow
    ).delete()
    session.flush()

    csv = _make_csv(header_col="STN_q", value=flow)
    payload = f"<HTML><BODY><PRE>\n{csv}</PRE></BODY></HTML>"
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    stored = (
        session.query(Observation.value)
        .filter_by(source_id=sample_source.id, data_type=DataType.flow)
        .scalar()
    )
    assert stored is not None
    assert math.isclose(stored, flow, rel_tol=1e-9), (
        f"HTML-wrapped CSV: flow={flow} stored as {stored}"
    )
