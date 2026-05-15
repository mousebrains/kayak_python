"""Property-based tests for the USACE CDA parser (T2.2 — second parser).

Mirrors the structure of ``test_nwps_properties.py`` but adapted to the
USACE CDA endpoint's quirks: timezone-in-URL hard requirement, the
parameter→DataType mapping table, and the [ts, val, quality] triple
entry shape.

Settings choices match the nwps file (derandomize, no DB, deadline=None,
50 examples) — keeps Hypothesis runs deterministic and CI-fast.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kayak.db.models import Observation, Source
from kayak.parsers.usace_cda import USACECDAParser

_HSETTINGS = settings(
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# The five parameters the parser recognizes. Anything else gets logged
# and skipped without producing an Observation.
_KNOWN_PARAMETERS = (
    "Flow-Out",
    "Flow-In",
    "Flow-Spill",
    "Elev-Forebay",
    "Elev-Tailwater",
)

_URL_WITH_GMT = "https://example.com/cda?timezone=GMT&backward=2d&forward=0d"


def _recent_timestamp(hours_back: int = 1) -> str:
    when = datetime.now(UTC) - timedelta(hours=hours_back)
    return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _future_timestamp(hours_ahead: int = 1) -> str:
    when = datetime.now(UTC) + timedelta(hours=hours_ahead)
    return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _make_payload(
    *,
    station: str = "TEST",
    parameter: str = "Flow-Out",
    entries: list[list[object]] | None = None,
) -> str:
    """Build a CDA-shaped JSON body with one station + one timeseries."""
    return json.dumps(
        {
            station: {
                "name": "Test Station",
                "timeseries": {
                    f"{station}.{parameter}.Inst.0.0.Best": {
                        "parameter": parameter,
                        "units": "cfs",
                        "values": entries or [],
                    },
                },
            },
        }
    )


def _new_parser(session, sample_source: Source, url: str = _URL_WITH_GMT) -> USACECDAParser:
    return USACECDAParser(url=url, session=session, source_id=sample_source.id)


# Property 1 -----------------------------------------------------------


@_HSETTINGS
@given(
    parameter=st.sampled_from((*_KNOWN_PARAMETERS, "Wind-Speed", "Random-Param", "")),
    value=st.one_of(
        st.none(),
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    ),
    quality=st.integers(min_value=0, max_value=255),
)
def test_parse_never_raises_on_well_formed_json(session, sample_source, parameter, value, quality):
    """The parser handles any parameter + value + quality without raising.

    Exercises the same crash-resistance invariant as the nwps version,
    sized to USACE CDA's input shape. Catches future refactors that
    might assume "parameter" is non-empty, "value" is non-null, or
    "quality" is bounded.
    """
    payload = _make_payload(
        parameter=parameter,
        entries=[[_recent_timestamp(), value, quality]],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)  # no exception → test passes


# Property 2 -----------------------------------------------------------


@_HSETTINGS
@given(
    # Strategy: URLs WITHOUT `timezone=GMT`. Other timezone tokens are
    # the realistic confound (PST/EST/UTC...) — the parser hard-rejects
    # anything that's not the literal `timezone=GMT` token.
    tz_token=st.sampled_from(["timezone=PST", "timezone=UTC", "tz=GMT", "", "timezone=EST"]),
)
def test_url_without_gmt_timezone_raises(session, sample_source, tz_token):
    """Parser refuses URLs without ``timezone=GMT``.

    USACE CDA's server defaults to PST if `timezone` is unset; the
    parser stamps UTC on naive timestamps, so a missing GMT would
    silently shift every observation 8 hours back. Hard-fail is the
    only safe contract.
    """
    bad_url = f"https://example.com/cda?{tz_token}&backward=2d&forward=0d"
    parser = _new_parser(session, sample_source, url=bad_url)
    payload = _make_payload(entries=[[_recent_timestamp(), 100.0, 0]])
    with pytest.raises(ValueError, match="timezone=GMT"):
        parser.parse(payload)


# Property 3 -----------------------------------------------------------


@_HSETTINGS
@given(hours_ahead=st.integers(min_value=1, max_value=24 * 30))
def test_future_timestamps_never_stored(session, sample_source, hours_ahead):
    """``timestamp > now`` rows are dropped (CDA serves observed-only here)."""
    payload = _make_payload(
        entries=[[_future_timestamp(hours_ahead), 100.0, 0]],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Future input (+{hours_ahead}h) produced {len(obs)} observations"


# Property 4 -----------------------------------------------------------


@_HSETTINGS
@given(
    # Values from outside ``_PARAM_MAP``. Empty string is the common
    # "parameter field missing" malformation; the others are fields
    # USACE actually serves but the parser doesn't track (river temp,
    # gate state, etc.).
    parameter=st.sampled_from(
        ["", "Temp-Water", "Gate-Position", "Precip-Inc", "Flow-Average", "PARAMS_RANDOM"]
    ),
)
def test_unknown_parameter_never_stored(session, sample_source, parameter):
    """A `parameter` outside the recognized map skips silently — no Observation written."""
    payload = _make_payload(
        parameter=parameter,
        entries=[[_recent_timestamp(), 100.0, 0]],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Unknown parameter {parameter!r} produced {len(obs)} observations"


# Property 5 -----------------------------------------------------------


@_HSETTINGS
@given(parameter=st.sampled_from(_KNOWN_PARAMETERS))
def test_null_value_never_stored(session, sample_source, parameter):
    """``value=None`` in a triple is treated as missing — skipped, no row.

    Cross-checks property 4 by holding the parameter constant on a
    *known* mapping (so the only reason for the row to skip is the
    null value, not the param-map filter).
    """
    payload = _make_payload(
        parameter=parameter,
        entries=[[_recent_timestamp(), None, 0]],
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Null value for {parameter} produced {len(obs)} observations"
