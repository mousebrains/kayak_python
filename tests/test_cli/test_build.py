"""Tests for kayak.cli.build output generators."""

from unittest import mock

from kayak.cli.build import _build_csv, _build_html_table, _build_text
from kayak.db.models import Gauge, Reach

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


def _make_reaches(session, count=1):
    """Create *count* minimal reaches with gauges for builder tests."""
    reaches = []
    for i in range(count):
        gauge = Gauge(name=f"gauge_{i}")
        session.add(gauge)
        session.flush()

        reach = Reach(
            name=f"reach_{i}",
            display_name=f"River {i}",
            sort_name=f"River {i}",
            gauge_id=gauge.id,
        )
        session.add(reach)
        session.flush()
        reaches.append(reach)
    return reaches


def test_build_csv_empty_reaches(session):
    """_build_csv with no reaches returns a header-only CSV."""
    with mock.patch("kayak.cli.build._get_row_data", return_value={}):
        result = _build_csv([], COLS, "", set(), {})

    lines = result.strip().splitlines()
    assert len(lines) == 1
    assert "Name" in lines[0]
    assert "Flow" in lines[0]


def test_build_csv_with_reaches(session):
    """_build_csv includes a data row for each reach."""
    reaches = _make_reaches(session, count=2)
    fake_rows = [
        {"display_name": "River 0", "flow": 1200.5},
        {"display_name": "River 1", "flow": 800.0},
    ]

    with mock.patch("kayak.cli.build._get_row_data", side_effect=fake_rows):
        result = _build_csv(reaches, COLS, "", set(), {})

    lines = result.strip().splitlines()
    # header + 2 data rows
    assert len(lines) == 3
    assert "River 0" in lines[1]
    assert "River 1" in lines[2]


def test_build_text_fixed_width(session):
    """_build_text produces fixed-width output with a separator line."""
    reaches = _make_reaches(session, count=1)
    fake_row = {"display_name": "Deschutes", "flow": 1500.0}

    with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
        result = _build_text(reaches, COLS, "", set(), {})

    lines = result.splitlines()
    # header, separator, data row
    assert len(lines) >= 3
    assert "---" in lines[1]
    assert "Deschutes" in lines[2]


def test_build_html_table_produces_table(session):
    """_build_html_table wraps output in <table> tags."""
    reaches = _make_reaches(session, count=1)
    fake_row = {"display_name": "Clackamas", "flow": 900.0}

    with (
        mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        mock.patch("kayak.cli.build._build_sparkline", return_value=""),
    ):
        result, _letters = _build_html_table(reaches, COLS, set(), {}, {})

    assert "<table" in result
    assert "</table>" in result
    assert "Name" in result
    assert "Flow" in result


def test_build_html_table_includes_flow_value(session):
    """_build_html_table renders flow values in the table."""
    reaches = _make_reaches(session, count=1)
    fake_row = {"display_name": "Sandy", "flow": 750.0}

    with (
        mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        mock.patch("kayak.cli.build._build_sparkline", return_value=""),
    ):
        result, _letters = _build_html_table(reaches, COLS, set(), {}, {})

    assert "750" in result


def test_build_html_table_includes_name_link(session):
    """_build_html_table wraps the reach name in a link to description.php."""
    reaches = _make_reaches(session, count=1)
    fake_row = {"display_name": "White Salmon", "flow": 1200.0}

    with (
        mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        mock.patch("kayak.cli.build._build_sparkline", return_value=""),
    ):
        result, _letters = _build_html_table(reaches, COLS, set(), {}, {})

    assert "description.php" in result
    assert "White Salmon" in result
