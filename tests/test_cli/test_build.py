"""Tests for kayak.cli.build output generators."""

from unittest import mock

from kayak.cli.build import _build_csv, _build_html, _build_text
from kayak.db.models import Gauge, Section

COLS = [
    {
        "sort_key": 1,
        "use": "cht",
        "type": "name",
        "field": "display_name",
        "length": 30,
        "name_text": "Name",
        "name_html": "Name",
    },
    {
        "sort_key": 2,
        "use": "cht",
        "type": "flow",
        "field": "flow",
        "length": 10,
        "name_text": "Flow",
        "name_html": "Flow",
    },
]


def _make_sections(session, count=1):
    """Create *count* minimal sections with gauges for builder tests."""
    sections = []
    for i in range(count):
        gauge = Gauge(name=f"gauge_{i}")
        session.add(gauge)
        session.flush()

        sec = Section(
            name=f"sec_{i}",
            display_name=f"River {i}",
            sort_name=f"River {i}",
            gauge_id=gauge.id,
        )
        session.add(sec)
        session.flush()
        sections.append(sec)
    return sections


def test_build_csv_empty_sections(session):
    """_build_csv with no sections returns a header-only CSV."""
    # Patch _get_row_data so builder does not try to hit data_db
    with mock.patch("kayak.cli.build._get_row_data", return_value={}):
        result = _build_csv(session, [], COLS, "")

    lines = result.strip().splitlines()
    assert len(lines) == 1
    assert "Name" in lines[0]
    assert "Flow" in lines[0]


def test_build_csv_with_sections(session):
    """_build_csv includes a data row for each section."""
    sections = _make_sections(session, count=2)
    fake_rows = [
        {"display_name": "River 0", "flow": 1200.5},
        {"display_name": "River 1", "flow": 800.0},
    ]

    with mock.patch("kayak.cli.build._get_row_data", side_effect=fake_rows):
        result = _build_csv(session, sections, COLS, "")

    lines = result.strip().splitlines()
    # header + 2 data rows
    assert len(lines) == 3
    assert "River 0" in lines[1]
    assert "River 1" in lines[2]


def test_build_text_fixed_width(session):
    """_build_text produces fixed-width output with a separator line."""
    sections = _make_sections(session, count=1)
    fake_row = {"display_name": "Deschutes", "flow": 1500.0}

    with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
        result = _build_text(session, sections, COLS, "")

    lines = result.splitlines()
    # header, separator, data row
    assert len(lines) >= 3
    assert "---" in lines[1]
    assert "Deschutes" in lines[2]


def test_build_html_produces_table(session):
    """_build_html wraps output in <table> tags."""
    sections = _make_sections(session, count=1)
    fake_row = {"display_name": "Clackamas", "flow": 900.0}

    with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
        result = _build_html(session, sections, COLS, "")

    assert "<table" in result
    assert "</table>" in result
    assert "<th>Name</th>" in result
    assert "<th>Flow</th>" in result


def test_build_html_includes_flow_link(session):
    """_build_html wraps flow values in an <a> tag with ?f= query param."""
    sections = _make_sections(session, count=1)
    fake_row = {"display_name": "Sandy", "flow": 750.0}

    with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
        result = _build_html(session, sections, COLS, "")

    assert "?f=" in result
    assert "750" in result


def test_build_html_includes_name_link(session):
    """_build_html wraps the section name in a description link."""
    sections = _make_sections(session, count=1)
    fake_row = {"display_name": "White Salmon", "flow": ""}

    with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
        result = _build_html(session, sections, COLS, "")

    assert "?D=" in result
    assert "White Salmon" in result
