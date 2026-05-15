"""Property-based tests for the NWRFC textPlot parser (T2.2 — sixth parser).

NWRFC textPlot is the only parser in this set that walks HTML via regex
rather than a structured DOM / JSON / CSV path — that makes it the
most fragile to format drift and the highest-value target for property
testing. The contract surface:

- Station is the ``?id=…`` query parameter on the URL.
- Column schema is inferred from a header row whose cells match
  ``_LABEL_TO_DTYPE`` (``stage`` → gauge, ``discharge`` → flow,
  ``inflow`` → inflow). Pages without that header fall back to a
  1-column inflow/flow heuristic.
- Future timestamps are dropped; negative values are dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kayak.db.models import Observation, Source
from kayak.parsers.nwrfc_textplot import NWRFCTextPlotParser

_HSETTINGS = settings(
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_STATION = "TSTLID"
_BASE_URL = f"test://nwrfc/textplot?id={_STATION}&pe=HG"


def _recent_textplot_date(hours_back: int = 1) -> str:
    """NWRFC textPlot uses ``YYYY-MM-DD HH:MM``."""
    when = datetime.now(UTC) - timedelta(hours=hours_back)
    return when.replace(microsecond=0).strftime("%Y-%m-%d %H:%M")


def _future_textplot_date(hours_ahead: int = 1) -> str:
    when = datetime.now(UTC) + timedelta(hours=hours_ahead)
    return when.replace(microsecond=0).strftime("%Y-%m-%d %H:%M")


def _make_single_column_body(
    *, when: str, value: str | float, column_label: str = "Discharge"
) -> str:
    """One-column observed-only textPlot HTML body.

    The parser's no-header fallback path emits ``[DataType.flow]`` (or
    ``[DataType.inflow]`` if "inflow" appears verbatim in the body),
    so this shape exercises the simplest end-to-end path.
    """
    return (
        "<html><body><table>\n"
        f"<tr><td>{column_label}</td></tr>\n"
        f"<tr><td>{when}</td><td>{value}</td></tr>\n"
        "</table></body></html>\n"
    )


def _new_parser(session, sample_source: Source) -> NWRFCTextPlotParser:
    return NWRFCTextPlotParser(
        url=_BASE_URL,
        session=session,
        source_id=sample_source.id,
        source_map={_STATION: sample_source.id},
    )


# Property 1 -----------------------------------------------------------


@_HSETTINGS
@given(
    column_label=st.sampled_from(["Discharge", "Stage", "Inflow", "Unknown", ""]),
    value=st.one_of(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False).map(str),
        st.sampled_from(["", "  ", "n/a", "—"]),
    ),
)
def test_parse_never_raises_on_well_formed_body(session, sample_source, column_label, value):
    """Parser handles any column-label x value combo without raising.

    Includes the no-header / unknown-label fallback path (where
    ``_infer_value_columns`` returns 1-column flow/inflow) and the
    happy paths (Stage / Discharge / Inflow).
    """
    payload = _make_single_column_body(
        when=_recent_textplot_date(),
        value=value,
        column_label=column_label,
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)


# Property 2 -----------------------------------------------------------


@_HSETTINGS
@given(hours_ahead=st.integers(min_value=1, max_value=24 * 30))
def test_future_timestamps_never_stored(session, sample_source, hours_ahead):
    """``datetime > now`` rows are dropped (observed-only endpoint)."""
    payload = _make_single_column_body(
        when=_future_textplot_date(hours_ahead),
        value=100.0,
        column_label="Discharge",
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Future input (+{hours_ahead}h) produced {len(obs)} observations"


# Property 3 -----------------------------------------------------------


@_HSETTINGS
@given(value=st.floats(min_value=-1e6, max_value=-1e-6, allow_nan=False, allow_infinity=False))
def test_negative_values_never_stored(session, sample_source, value):
    """Negative values are unphysical for stage / discharge / inflow and dropped.

    The parser's ``val < 0`` continue applies to every column. Tested
    on the discharge column; same regex path handles all three.
    """
    payload = _make_single_column_body(
        when=_recent_textplot_date(),
        value=value,
        column_label="Discharge",
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Negative value {value} produced {len(obs)} observations"


# Property 4 -----------------------------------------------------------


@_HSETTINGS
@given(
    body=st.sampled_from(
        [
            "",
            "<html></html>",
            "<html><body>no table here</body></html>",
            "<html><body><table></table></body></html>",
            "<html><body><table><tr></tr></table></body></html>",
        ]
    ),
)
def test_empty_or_tableless_body_produces_no_obs(session, sample_source, body):
    """A body without a parseable ``<tr><td>datetime</td><td>value</td></tr>`` is a no-op.

    Catches a regression that would mis-parse error pages / outage
    responses as if they were observations.
    """
    parser = _new_parser(session, sample_source)
    parser.parse(body)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Empty body produced {len(obs)} observations"


# Property 5 -----------------------------------------------------------


@_HSETTINGS
@given(
    # `pe=HG` URLs without a matching `id=` query param. The parser
    # extracts station via regex; the dump_to_db without a source_map
    # entry hits the auto-create path. With source_map empty here, the
    # parser would auto-create a Source — but since we pass source_id,
    # the fallback returns sample_source.id. The property: even when
    # the URL has no ``id=`` parameter, the parser still proceeds (it
    # doesn't crash) and uses source_id fallback.
    url_query=st.sampled_from(["pe=HG", "pe=QR&format=html", "", "id=&pe=HG"]),
)
def test_url_without_id_still_parses_without_crashing(session, sample_source, url_query):
    """Station-extraction failure must not crash the parser.

    Real-world failures: redirect to a search page, error response that
    drops the query string. ``_extract_station`` returns ``""`` in those
    cases; ``dump_to_db`` falls back to ``self.source_id``. The data
    still flows; we just verify the path doesn't raise.
    """
    parser = NWRFCTextPlotParser(
        url=f"test://nwrfc/textplot?{url_query}",
        session=session,
        source_id=sample_source.id,
    )
    payload = _make_single_column_body(
        when=_recent_textplot_date(),
        value=100.0,
        column_label="Discharge",
    )
    parser.parse(payload)  # no exception → test passes
