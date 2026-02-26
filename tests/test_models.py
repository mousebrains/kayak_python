"""Tests for ORM models and basic database operations."""

from datetime import datetime, timezone

import pytest

from kayak.db.models import (
    CalcExpression,
    ClassDescription,
    DataType,
    FetchUrl,
    FlowLevel,
    Gauge,
    GaugeSource,
    Guidebook,
    LatestObservation,
    Observation,
    Page,
    PageAction,
    Rating,
    RatingData,
    Section,
    SectionClass,
    SectionLevel,
    Source,
    State,
)


def test_create_gauge(session):
    g = Gauge(name="test_gauge", usgs_id="12345678")
    session.add(g)
    session.flush()

    result = session.get(Gauge, g.id)
    assert result is not None
    assert result.name == "test_gauge"
    assert result.usgs_id == "12345678"


def test_gauge_unique_name(session):
    session.add(Gauge(name="dup"))
    session.flush()
    session.add(Gauge(name="dup"))
    with pytest.raises(Exception):
        session.flush()


def test_create_source_with_fetch_url(session):
    fu = FetchUrl(url="https://example.com", parser="usgs", is_active=True)
    session.add(fu)
    session.flush()

    src = Source(name="src1", agency="USGS", fetch_url_id=fu.id)
    session.add(src)
    session.flush()

    result = session.get(Source, src.id)
    assert result.name == "src1"
    assert result.fetch_url.url == "https://example.com"


def test_create_source_with_calc_expression(session):
    ce = CalcExpression(data_type=DataType.flow, expression="a + b")
    session.add(ce)
    session.flush()

    src = Source(name="calc_src", calc_expression_id=ce.id)
    session.add(src)
    session.flush()

    result = session.get(Source, src.id)
    assert result.calc_expression.expression == "a + b"


def test_gauge_source_junction(session, sample_source, sample_gauge):
    session.add(GaugeSource(gauge_id=sample_gauge.id, source_id=sample_source.id))
    session.flush()

    gauge = session.get(Gauge, sample_gauge.id)
    assert len(gauge.sources) == 1
    assert gauge.sources[0].name == "test_source"


def test_create_observation(session, sample_source):
    now = datetime.now(timezone.utc)
    obs = Observation(
        source_id=sample_source.id,
        observed_at=now,
        data_type=DataType.flow,
        value=1500.0,
    )
    session.add(obs)
    session.flush()

    result = session.query(Observation).first()
    assert result.source_id == sample_source.id
    assert result.data_type == DataType.flow
    assert result.value == 1500.0


def test_observation_composite_pk(session, sample_source):
    """Duplicate source_id/observed_at/data_type should conflict."""
    now = datetime.now(timezone.utc)
    session.add(Observation(
        source_id=sample_source.id, observed_at=now,
        data_type=DataType.gauge, value=5.0,
    ))
    session.flush()

    session.add(Observation(
        source_id=sample_source.id, observed_at=now,
        data_type=DataType.gauge, value=6.0,
    ))
    with pytest.raises(Exception):
        session.flush()


def test_latest_observation(session, sample_source):
    now = datetime.now(timezone.utc)
    lo = LatestObservation(
        source_id=sample_source.id,
        data_type=DataType.flow,
        observed_at=now,
        value=100.0,
        delta_per_hour=2.5,
    )
    session.add(lo)
    session.flush()

    result = session.query(LatestObservation).first()
    assert result.delta_per_hour == 2.5


def test_create_section(session, sample_section):
    result = session.get(Section, sample_section.id)
    assert result.display_name == "Test River - Upper"
    assert result.gauge is not None


def test_section_state_junction(session, sample_section):
    state = State(name="OR", abbreviation="OR")
    session.add(state)
    session.flush()
    sample_section.states.append(state)
    session.flush()

    section = session.get(Section, sample_section.id)
    assert len(section.states) == 1
    assert section.states[0].name == "OR"


def test_section_class(session, sample_section):
    sc = SectionClass(
        section_id=sample_section.id, name="III",
        low=500.0, low_data_type=DataType.flow,
        high=2000.0, high_data_type=DataType.flow,
    )
    session.add(sc)
    session.flush()

    section = session.get(Section, sample_section.id)
    assert len(section.classes) == 1
    assert section.classes[0].name == "III"


def test_section_level(session, sample_section):
    sl = SectionLevel(
        section_id=sample_section.id, level=FlowLevel.okay,
        low=800.0, low_data_type=DataType.flow,
    )
    session.add(sl)
    session.flush()

    section = session.get(Section, sample_section.id)
    assert len(section.levels) == 1
    assert section.levels[0].level == FlowLevel.okay


def test_rating_and_data(session):
    rating = Rating(url="https://example.com/rating", parser="usgs")
    session.add(rating)
    session.flush()

    session.add(RatingData(rating_id=rating.id, gauge_height_ft=1.0, flow_cfs=100.0))
    session.add(RatingData(rating_id=rating.id, gauge_height_ft=2.0, flow_cfs=400.0))
    session.flush()

    result = session.get(Rating, rating.id)
    assert len(result.data_points) == 2


def test_gauge_rating_relationship(session, sample_gauge):
    rating = Rating(url="https://example.com/rating")
    session.add(rating)
    session.flush()

    sample_gauge.rating_id = rating.id
    session.flush()

    gauge = session.get(Gauge, sample_gauge.id)
    assert gauge.rating is not None
    assert gauge.rating.id == rating.id


def test_class_description(session):
    cd = ClassDescription(name="III", description="Intermediate")
    session.add(cd)
    session.flush()

    result = session.get(ClassDescription, "III")
    assert result.description == "Intermediate"


def test_guidebook(session, sample_section):
    gb = Guidebook(title="Oregon Kayaking", author="John Doe")
    session.add(gb)
    session.flush()

    sample_section.guidebooks.append(gb)
    session.flush()

    section = session.get(Section, sample_section.id)
    assert len(section.guidebooks) == 1


def test_fetch_url(session):
    fu = FetchUrl(
        url="https://waterservices.usgs.gov/nwis/iv/?format=rdb",
        parser="usgs.rdb",
        is_active=True,
    )
    session.add(fu)
    session.flush()

    result = session.query(FetchUrl).first()
    assert result.parser == "usgs.rdb"
    assert result.is_active is True


def test_page(session):
    p = Page(
        name="main", action=PageAction.PAGE,
        body="<html>Hello</html>", mimetype="text/html"
    )
    session.add(p)
    session.flush()

    result = session.get(Page, "main")
    assert result.action == PageAction.PAGE
    assert "Hello" in result.body


def test_data_type_enum_values():
    """Verify DataType enum uses lowercase values matching production."""
    assert DataType.flow.value == "flow"
    assert DataType.gauge.value == "gauge"
    assert DataType.inflow.value == "inflow"
    assert DataType.temperature.value == "temperature"
