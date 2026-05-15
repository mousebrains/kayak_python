"""Property-based tests for the NWRFC XML parser (T2.2 — third parser).

NWRFC XML has three recognized tags (stage / discharge / inflow), each
with its own units allowlist (substring match on the ``units=``
attribute) and per-tag non-negativity rule. The parser also rejects
future timestamps, malformed XML, and XXE-style payloads (handled by
lxml's secured parser).

Settings mirror the other property-test files — derandomized, no
Hypothesis DB, 50 examples per property.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kayak.db.models import DataType, Observation, Source
from kayak.parsers.nwrfc_xml import NWRFCXMLParser

_HSETTINGS = settings(
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Tags the parser maps to a DataType via its _TAG_HANDLERS table.
_KNOWN_TAGS = ("stage", "discharge", "inflow")
# Units that pass each tag's substring check.
_VALID_STAGE_UNITS = ("feet", "ft")
_VALID_FLOW_UNITS = ("cubic feet per second", "cfs")
# Units that should *fail* the substring check for any flow-shaped tag.
_INVALID_FLOW_UNITS = ("meters", "m3/s", "", "kg")


def _recent_iso(hours_back: int = 1) -> str:
    when = datetime.now(UTC) - timedelta(hours=hours_back)
    return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _future_iso(hours_ahead: int = 1) -> str:
    when = datetime.now(UTC) + timedelta(hours=hours_ahead)
    return when.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _make_xml(
    *,
    station: str = "TESTLID",
    when_iso: str,
    tag: str = "discharge",
    units: str = "cfs",
    value: float = 100.0,
) -> str:
    """Build a NWRFC-shaped XML body with one site + one observed entry."""
    return (
        '<?xml version="1.0"?>\n'
        "<forecast>\n"
        f'  <SiteData id="{station}">\n'
        "    <observedData>\n"
        f"      <dataDateTime>{when_iso}</dataDateTime>\n"
        f'      <{tag} units="{units}">{value}</{tag}>\n'
        "    </observedData>\n"
        "  </SiteData>\n"
        "</forecast>\n"
    )


def _new_parser(session, sample_source: Source) -> NWRFCXMLParser:
    return NWRFCXMLParser(
        url="test://nwrfc/observed.xml",
        session=session,
        source_id=sample_source.id,
    )


# Property 1 -----------------------------------------------------------


@_HSETTINGS
@given(
    tag=st.sampled_from((*_KNOWN_TAGS, "temperature", "wind", "RANDOM_TAG", "")),
    units=st.sampled_from(
        (*_VALID_STAGE_UNITS, *_VALID_FLOW_UNITS, *_INVALID_FLOW_UNITS, "FEET", "Ft")
    ),
    value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
def test_parse_never_raises_on_well_formed_xml(session, sample_source, tag, units, value):
    """Parser handles any tag + units + value combo without raising.

    The tag/units cross-product is the most likely place a refactor
    could regress — a future cleanup that assumes tag is non-empty or
    units always present would crash here.
    """
    # Skip the empty-tag case for the XML builder (can't have an empty
    # element name); Hypothesis sampling will hit the other 9 anyway.
    if tag == "":
        return
    payload = _make_xml(when_iso=_recent_iso(), tag=tag, units=units, value=value)
    parser = _new_parser(session, sample_source)
    parser.parse(payload)  # no exception → test passes


# Property 2 -----------------------------------------------------------


@_HSETTINGS
@given(hours_ahead=st.integers(min_value=1, max_value=24 * 30))
def test_future_timestamps_never_stored(session, sample_source, hours_ahead):
    """``dataDateTime > now`` rows are dropped.

    NWRFC publishes both observed (past) and forecast (future) data in
    different blocks; this parser is the observed-data path so any
    future timestamp is a malformation / clock-skew.
    """
    payload = _make_xml(
        when_iso=_future_iso(hours_ahead),
        tag="discharge",
        units="cfs",
        value=100.0,
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Future input (+{hours_ahead}h) produced {len(obs)} observations"


# Property 3 -----------------------------------------------------------


@_HSETTINGS
@given(value=st.floats(min_value=-1e6, max_value=-1e-6, allow_nan=False, allow_infinity=False))
def test_negative_inflow_never_stored(session, sample_source, value):
    """``inflow`` is gated on non-negativity (the only tag with that rule).

    A negative inflow is unphysical for a reservoir; the parser
    explicitly drops these rows even when the unit and timestamp are
    otherwise valid.
    """
    payload = _make_xml(
        when_iso=_recent_iso(),
        tag="inflow",
        units="cfs",
        value=value,
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    inflows = (
        session.query(Observation)
        .filter_by(source_id=sample_source.id, data_type=DataType.inflow)
        .all()
    )
    assert inflows == [], f"Negative inflow {value} produced {len(inflows)} rows"


# Property 4 -----------------------------------------------------------


@_HSETTINGS
@given(
    tag=st.sampled_from(("temperature", "wind", "RANDOM_TAG", "pressure", "humidity")),
)
def test_unknown_tag_never_stored(session, sample_source, tag):
    """A tag outside _TAG_HANDLERS yields no Observation, regardless of unit/value."""
    payload = _make_xml(
        when_iso=_recent_iso(),
        tag=tag,
        units="cfs",
        value=100.0,
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], f"Unknown tag {tag!r} produced {len(obs)} observations"


# Property 5 -----------------------------------------------------------


@_HSETTINGS
@given(units=st.sampled_from(_INVALID_FLOW_UNITS))
def test_discharge_with_wrong_units_never_stored(session, sample_source, units):
    """``discharge units=…`` outside the ``("cubic", "cfs")`` substring set is dropped.

    Catches metric units (m3/s, etc.) and unit-stripped malformations.
    Stage's units check is independent — tested by symmetry, not
    repeated here (only stage's accept-set is different).
    """
    payload = _make_xml(
        when_iso=_recent_iso(),
        tag="discharge",
        units=units,
        value=100.0,
    )
    parser = _new_parser(session, sample_source)
    parser.parse(payload)
    obs = session.query(Observation).filter_by(source_id=sample_source.id).all()
    assert obs == [], (
        f"discharge with units={units!r} produced {len(obs)} observations — should be dropped"
    )
