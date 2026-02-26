"""Tests for ORM models and basic database operations."""

from datetime import datetime, timezone

from kayak.db.models import (
    BuilderColumn,
    DataType,
    DescriptionField,
    Latest,
    Master,
    Measurement,
    Page,
    PageAction,
    Parameter,
    RatingTable,
    URLParse,
)


def test_create_master(session):
    m = Master(hash_value="0", display_name="Test River", state="Oregon")
    session.add(m)
    session.flush()

    result = session.get(Master, "0")
    assert result is not None
    assert result.display_name == "Test River"
    assert result.state == "Oregon"


def test_create_measurement(session):
    now = datetime.now(timezone.utc)
    m = Measurement(
        station="12345678",
        data_type=DataType.FLOW,
        time=now,
        value=1500.0,
    )
    session.add(m)
    session.flush()

    result = session.query(Measurement).first()
    assert result.station == "12345678"
    assert result.data_type == DataType.FLOW
    assert result.value == 1500.0


def test_measurement_unique_constraint(session):
    """Duplicate station/type/time should conflict."""
    now = datetime.now(timezone.utc)
    m1 = Measurement(station="s1", data_type=DataType.GAGE, time=now, value=5.0)
    session.add(m1)
    session.flush()

    # Same station/type/time — should raise on flush
    m2 = Measurement(station="s1", data_type=DataType.GAGE, time=now, value=6.0)
    session.add(m2)
    import pytest
    with pytest.raises(Exception):
        session.flush()


def test_create_latest(session):
    now = datetime.now(timezone.utc)
    lat = Latest(
        station="s1",
        data_type=DataType.FLOW,
        time=now,
        value=100.0,
        delta=2.5,
    )
    session.add(lat)
    session.flush()

    result = session.query(Latest).first()
    assert result.delta == 2.5


def test_parameter(session):
    p = Parameter(ident="rootDir", value="/home/tpw/kayaking")
    session.add(p)
    session.flush()

    result = session.get(Parameter, "rootDir")
    assert result.value == "/home/tpw/kayaking"


def test_url_parse(session):
    u = URLParse(url="https://example.com/data", parser="usgs", hours="")
    session.add(u)
    session.flush()

    result = session.query(URLParse).first()
    assert result.parser == "usgs"


def test_description_field(session):
    d = DescriptionField(
        sort_key=100, column_name="class", type="text",
        prefix="Class", suffix="<br />"
    )
    session.add(d)
    session.flush()

    result = session.get(DescriptionField, 100)
    assert result.column_name == "class"


def test_builder_column(session):
    b = BuilderColumn(
        sort_key=500, use="hct", type="flow", field="flow",
        length=7, name_text="Flow", name_html="Flow<br/>CFS"
    )
    session.add(b)
    session.flush()

    result = session.get(BuilderColumn, 500)
    assert result.field == "flow"


def test_rating_table(session):
    session.add(RatingTable(db_name="test", feet=1.0, cfs=100.0))
    session.add(RatingTable(db_name="test", feet=2.0, cfs=400.0))
    session.flush()

    rows = session.query(RatingTable).filter_by(db_name="test").order_by(RatingTable.feet).all()
    assert len(rows) == 2
    assert rows[0].cfs == 100.0
    assert rows[1].cfs == 400.0


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
