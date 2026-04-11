"""Tests for kayak.cli.build output generators."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest import mock

from kayak.cli.build import (
    _atomic_write,
    _build_csv,
    _build_geojson,
    _build_html_table,
    _build_letter_nav,
    _build_nav,
    _build_page,
    _build_sparkline,
    _build_text,
    _get_row_data,
    _levels_key,
)
from kayak.db.models import (
    DataType,
    FlowLevel,
    Gauge,
    LatestGaugeObservation,
    Observation,
    Reach,
    ReachLevel,
    Source,
)

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
    {
        "sort_key": 3,
        "use": "cht",
        "type": "gage",
        "field": "gage",
        "length": 10,
        "name_text": "Gage",
        "name_html": "Gage",
    },
    {
        "sort_key": 4,
        "use": "cht",
        "type": "date",
        "field": "time",
        "length": 14,
        "name_text": "Time",
        "name_html": "Time",
    },
    {
        "sort_key": 5,
        "use": "cht",
        "type": "status",
        "field": "status",
        "length": 8,
        "name_text": "Status",
        "name_html": "Status",
    },
]

COLS_SIMPLE = COLS[:2]  # Just name + flow


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


# ---------------------------------------------------------------------------
# _get_row_data
# ---------------------------------------------------------------------------


class TestGetRowData:
    """Tests for the _get_row_data function."""

    def test_basic_reach_no_gauge(self, session):
        reach = Reach(name="no_gauge", display_name="No Gauge River")
        session.add(reach)
        session.flush()
        row = _get_row_data(reach, set(), {})
        assert row["display_name"] == "No Gauge River"
        assert "flow" not in row
        assert "time" not in row

    def test_reach_with_gauge_and_latest(self, session):
        gauge = Gauge(name="g1")
        session.add(gauge)
        session.flush()
        reach = Reach(name="r1", display_name="River 1", gauge_id=gauge.id)
        session.add(reach)
        session.flush()

        now = datetime.now(UTC)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=now,
            value=1500.0,
        )
        session.add(latest)
        session.flush()
        all_latest = {(gauge.id, DataType.flow): latest}

        row = _get_row_data(reach, set(), all_latest)
        assert row["flow"] == 1500.0
        assert row["time"] == now

    def test_estimated_flag_for_calculated_gauge(self, session):
        gauge = Gauge(name="calc_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="calc_r", display_name="Calc River", gauge_id=gauge.id)
        session.add(reach)
        session.flush()

        now = datetime.now(UTC)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=now,
            value=500.0,
        )
        session.add(latest)
        session.flush()

        row = _get_row_data(reach, {gauge.id}, {(gauge.id, DataType.flow): latest})
        assert row.get("is_estimated") is True

    def test_stale_detection(self, session):
        gauge = Gauge(name="stale_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="stale_r", display_name="Stale River", gauge_id=gauge.id)
        session.add(reach)
        session.flush()

        old_time = datetime.now(UTC) - timedelta(hours=72)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=old_time,
            value=300.0,
        )
        session.add(latest)
        session.flush()

        row = _get_row_data(reach, set(), {(gauge.id, DataType.flow): latest})
        assert row.get("stale") is True
        assert row.get("expired") is not True

    def test_expired_detection(self, session):
        gauge = Gauge(name="exp_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="exp_r", display_name="Expired River", gauge_id=gauge.id)
        session.add(reach)
        session.flush()

        old_time = datetime.now(UTC) - timedelta(days=10)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=old_time,
            value=100.0,
        )
        session.add(latest)
        session.flush()

        row = _get_row_data(reach, set(), {(gauge.id, DataType.flow): latest})
        assert row.get("expired") is True

    def test_level_classification(self, session):
        gauge = Gauge(name="lvl_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="lvl_r", display_name="Level River", gauge_id=gauge.id)
        session.add(reach)
        session.flush()
        rl = ReachLevel(
            reach_id=reach.id,
            level=FlowLevel.okay,
            low=500.0,
            low_data_type=DataType.flow,
            high=2000.0,
            high_data_type=DataType.flow,
        )
        session.add(rl)
        session.flush()

        now = datetime.now(UTC)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=now,
            value=1000.0,
        )
        session.add(latest)
        session.flush()

        row = _get_row_data(reach, set(), {(gauge.id, DataType.flow): latest})
        assert "flow_level" in row or "status" in row


# ---------------------------------------------------------------------------
# _build_sparkline
# ---------------------------------------------------------------------------


class TestBuildSparkline:
    def test_no_gauge_returns_empty(self, session):
        reach = Reach(name="ng", display_name="No Gauge")
        session.add(reach)
        session.flush()
        assert _build_sparkline(reach, {}) == ""

    def test_too_few_records_returns_empty(self, session):
        gauge = Gauge(name="few_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="few_r", gauge_id=gauge.id)
        session.add(reach)
        session.flush()
        source = Source(name="src")
        session.add(source)
        session.flush()

        now = datetime.now(UTC)
        obs = [
            Observation(
                source_id=source.id,
                observed_at=now - timedelta(hours=1),
                data_type=DataType.flow,
                value=100.0,
            ),
            Observation(source_id=source.id, observed_at=now, data_type=DataType.flow, value=200.0),
        ]
        for o in obs:
            session.add(o)
        session.flush()

        assert _build_sparkline(reach, {gauge.id: obs}) == ""

    def test_produces_svg(self, session):
        gauge = Gauge(name="spark_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="spark_r", gauge_id=gauge.id)
        session.add(reach)
        session.flush()
        source = Source(name="spark_src")
        session.add(source)
        session.flush()

        now = datetime.now(UTC)
        obs = []
        for i in range(20):
            o = Observation(
                source_id=source.id,
                observed_at=now - timedelta(hours=20 - i),
                data_type=DataType.flow,
                value=100.0 + i * 10,
            )
            session.add(o)
            obs.append(o)
        session.flush()

        result = _build_sparkline(reach, {gauge.id: obs})
        assert "<svg" in result
        assert "polyline" in result
        assert "spark" in result

    def test_custom_dimensions(self, session):
        gauge = Gauge(name="dim_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="dim_r", gauge_id=gauge.id)
        session.add(reach)
        session.flush()
        source = Source(name="dim_src")
        session.add(source)
        session.flush()

        now = datetime.now(UTC)
        obs = []
        for i in range(10):
            o = Observation(
                source_id=source.id,
                observed_at=now - timedelta(hours=10 - i),
                data_type=DataType.flow,
                value=50.0 + i * 5,
            )
            session.add(o)
            obs.append(o)
        session.flush()

        result = _build_sparkline(reach, {gauge.id: obs}, width=100, height=30)
        assert 'width="100"' in result
        assert 'height="30"' in result


# ---------------------------------------------------------------------------
# _build_csv / _build_text
# ---------------------------------------------------------------------------


class TestBuildCSV:
    def test_empty_reaches(self, session):
        with mock.patch("kayak.cli.build._get_row_data", return_value={}):
            result = _build_csv([], COLS_SIMPLE, "", set(), {})
        lines = result.strip().splitlines()
        assert len(lines) == 1
        assert "Name" in lines[0]
        assert "Flow" in lines[0]

    def test_with_reaches(self, session):
        reaches = _make_reaches(session, count=2)
        fake_rows = [
            {"display_name": "River 0", "flow": 1200.5},
            {"display_name": "River 1", "flow": 800.0},
        ]
        with mock.patch("kayak.cli.build._get_row_data", side_effect=fake_rows):
            result = _build_csv(reaches, COLS_SIMPLE, "", set(), {})
        lines = result.strip().splitlines()
        assert len(lines) == 3
        assert "River 0" in lines[1]
        assert "1200.5" in lines[1]

    def test_datetime_formatting(self, session):
        reaches = _make_reaches(session, count=1)
        dt = datetime(2026, 4, 10, 14, 30, tzinfo=UTC)
        cols = [*COLS_SIMPLE, COLS[3]]  # Add time column
        fake_row = {"display_name": "Test", "flow": 100.0, "time": dt}
        with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
            result = _build_csv(reaches, cols, "", set(), {})
        assert "2026-04-10 14:30" in result


class TestBuildText:
    def test_fixed_width(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Deschutes", "flow": 1500.0}
        with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
            result = _build_text(reaches, COLS_SIMPLE, "", set(), {})
        lines = result.splitlines()
        assert len(lines) >= 3
        assert "---" in lines[1]
        assert "Deschutes" in lines[2]

    def test_truncation_to_column_width(self, session):
        reaches = _make_reaches(session, count=1)
        long_name = "A" * 100
        fake_row = {"display_name": long_name, "flow": 100.0}
        with mock.patch("kayak.cli.build._get_row_data", return_value=fake_row):
            result = _build_text(reaches, COLS_SIMPLE, "", set(), {})
        data_line = result.splitlines()[2]
        # Name column is 30 chars wide — should be truncated
        assert len(data_line.rstrip()) <= 30 + 10  # name + flow columns


# ---------------------------------------------------------------------------
# _build_html_table
# ---------------------------------------------------------------------------


class TestBuildHTMLTable:
    def test_produces_table(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Clackamas", "flow": 900.0}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _letters = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "<table" in result
        assert "</table>" in result

    def test_includes_flow_value(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Sandy", "flow": 750.0}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "750" in result

    def test_name_link(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "White Salmon", "flow": 1200.0}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "description.php" in result
        assert "White Salmon" in result

    def test_expired_rows_filtered(self, session):
        reaches = _make_reaches(session, count=2)
        rows = [
            {"display_name": "Fresh River", "flow": 500.0},
            {"display_name": "Old River", "flow": 100.0, "expired": True},
        ]
        with (
            mock.patch("kayak.cli.build._get_row_data", side_effect=rows),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "Fresh River" in result
        assert "Old River" not in result

    def test_empty_data_filtered(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Empty River"}  # no flow/gage/temperature
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "Empty River" not in result

    def test_stale_class_applied(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Stale River", "flow": 200.0, "stale": True}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "stale" in result

    def test_letter_nav_generated(self, session):
        reaches = _make_reaches(session, count=3)
        for r, letter in zip(reaches, ["Alpha", "Beta", "Gamma"], strict=True):
            r.sort_name = letter
            r.display_name = f"{letter} River"
        session.flush()
        rows = [
            {"display_name": "Alpha River", "flow": 100.0},
            {"display_name": "Beta River", "flow": 200.0},
            {"display_name": "Gamma River", "flow": 300.0},
        ]
        with (
            mock.patch("kayak.cli.build._get_row_data", side_effect=rows),
        ):
            _, letters = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "A" in letters
        assert "B" in letters
        assert "G" in letters

    def test_gauge_grouping_rowspan(self, session):
        """Two reaches sharing the same gauge should merge gauge columns."""
        gauge = Gauge(name="shared_gauge")
        session.add(gauge)
        session.flush()
        r1 = Reach(name="r1", display_name="River A", sort_name="River A", gauge_id=gauge.id)
        r2 = Reach(name="r2", display_name="River B", sort_name="River B", gauge_id=gauge.id)
        session.add_all([r1, r2])
        session.flush()
        reaches = [r1, r2]
        rows = [
            {"display_name": "River A", "flow": 500.0, "status": "okay"},
            {"display_name": "River B", "flow": 500.0, "status": "okay"},
        ]
        with (
            mock.patch("kayak.cli.build._get_row_data", side_effect=rows),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert 'rowspan="2"' in result

    def test_status_column_rendering(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Status River", "flow": 100.0, "status": "high"}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "level-high" in result

    def test_status_value_html_escaped(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {
            "display_name": "XSS River",
            "flow": 100.0,
            "status": "<script>alert(1)</script>",
        }
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_data_label_html_escaped(self, session):
        evil_col = {**COLS_SIMPLE[1], "name_text": 'Flow"onmouseover="alert(1)'}
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Test", "flow": 100.0}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, [COLS_SIMPLE[0], evil_col], set(), {})
        # Quotes in label should be escaped so they can't break the attribute
        assert "&quot;" in result
        # Should NOT have an unescaped quote breaking out of data-label
        assert 'data-label="Flow&quot;' in result

    def test_estimated_tag(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Est River", "flow": 100.0, "is_estimated": True}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "(est)" in result

    def test_gage_value_rendering(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Gage River", "gage": 4.25}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "4.2" in result  # gage renders as .1f

    def test_time_column_rendering(self, session):
        reaches = _make_reaches(session, count=1)
        dt = datetime(2026, 4, 10, 14, 30, tzinfo=UTC)
        fake_row = {"display_name": "Time River", "flow": 100.0, "time": dt}
        with (
            mock.patch("kayak.cli.build._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "<time" in result
        assert "04/10 14:30" in result


# ---------------------------------------------------------------------------
# _build_geojson
# ---------------------------------------------------------------------------


class TestBuildGeoJSON:
    def test_linestring_from_geom(self, session):
        gauge = Gauge(name="geo_g")
        session.add(gauge)
        session.flush()
        reach = Reach(
            name="geo_r",
            display_name="Geo River",
            gauge_id=gauge.id,
            geom="-122.5 44.0,-122.4 44.1,-122.3 44.2",
        )
        session.add(reach)
        session.flush()

        now = datetime.now(UTC)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=now,
            value=100.0,
        )
        session.add(latest)
        session.flush()

        result = _build_geojson(
            [reach],
            set(),
            {(gauge.id, DataType.flow): latest},
        )
        import json

        data = json.loads(result)
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1
        assert data["features"][0]["geometry"]["type"] == "LineString"

    def test_point_from_coordinates(self, session):
        gauge = Gauge(name="pt_g")
        session.add(gauge)
        session.flush()
        reach = Reach(
            name="pt_r",
            display_name="Point River",
            gauge_id=gauge.id,
            latitude=Decimal("44.0"),
            longitude=Decimal("-122.5"),
        )
        session.add(reach)
        session.flush()

        now = datetime.now(UTC)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=now,
            value=50.0,
        )
        session.add(latest)
        session.flush()

        result = _build_geojson(
            [reach],
            set(),
            {(gauge.id, DataType.flow): latest},
        )
        import json

        data = json.loads(result)
        assert data["features"][0]["geometry"]["type"] == "Point"

    def test_expired_reaches_excluded(self, session):
        gauge = Gauge(name="exp_g2")
        session.add(gauge)
        session.flush()
        reach = Reach(
            name="exp_r2",
            display_name="Expired",
            gauge_id=gauge.id,
            latitude=Decimal("44.0"),
            longitude=Decimal("-122.5"),
        )
        session.add(reach)
        session.flush()

        old = datetime.now(UTC) - timedelta(days=10)
        latest = LatestGaugeObservation(
            gauge_id=gauge.id,
            data_type=DataType.flow,
            observed_at=old,
            value=50.0,
        )
        session.add(latest)
        session.flush()

        result = _build_geojson(
            [reach],
            set(),
            {(gauge.id, DataType.flow): latest},
        )
        import json

        data = json.loads(result)
        assert len(data["features"]) == 0

    def test_no_geometry_excluded(self, session):
        reach = Reach(name="nogeom", display_name="No Geom River")
        session.add(reach)
        session.flush()
        result = _build_geojson([reach], set(), {})
        import json

        data = json.loads(result)
        assert len(data["features"]) == 0


# ---------------------------------------------------------------------------
# Page construction helpers
# ---------------------------------------------------------------------------


class TestBuildNav:
    def test_includes_map_and_picker(self, session):
        result = _build_nav(["Oregon", "Washington"])
        assert "map.html" in result
        assert "picker.php" in result

    def test_active_state_highlighted(self, session):
        result = _build_nav(["Oregon", "Washington"], active_state="Oregon")
        assert 'class="active"' in result

    def test_non_nav_states_excluded(self, session):
        result = _build_nav(["Oregon", "Montana", "Wyoming"])
        assert "MT" not in result
        assert "WY" not in result


class TestBuildLetterNav:
    def test_empty_letters(self):
        assert _build_letter_nav([]) == ""

    def test_produces_links(self):
        result = _build_letter_nav(["A", "B", "C"])
        assert "#letter-A" in result
        assert "#letter-B" in result
        assert "#letter-C" in result
        assert "letter-nav" in result


class TestBuildPage:
    def test_complete_html_structure(self):
        result = _build_page(
            "<p>test</p>",
            "body{}",
            ["Oregon"],
            "Oregon",
            "Test Title",
        )
        assert "<!DOCTYPE html>" in result
        assert "<title>Test Title</title>" in result
        assert "body{}" in result  # CSS inlined
        assert "<p>test</p>" in result
        assert "River Levels" in result
        assert "Updated" in result

    def test_letter_nav_included(self):
        result = _build_page(
            "<p>test</p>",
            "",
            ["Oregon"],
            "Oregon",
            "Test",
            letters=["A", "B"],
        )
        assert "letter-nav" in result
        assert "#letter-A" in result


class TestLevelsKey:
    def test_no_levels(self, session):
        reach = Reach(name="nolev")
        session.add(reach)
        session.flush()
        assert _levels_key(reach) == ()

    def test_with_levels(self, session):
        reach = Reach(name="lvl")
        session.add(reach)
        session.flush()
        rl = ReachLevel(
            reach_id=reach.id,
            level=FlowLevel.okay,
            low=500.0,
            low_data_type=DataType.flow,
            high=2000.0,
            high_data_type=DataType.flow,
        )
        session.add(rl)
        session.flush()
        key = _levels_key(reach)
        assert key != ()
        assert len(key) == 1


# ---------------------------------------------------------------------------
# _atomic_write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_writes_and_reads_back(self, tmp_path):
        path = tmp_path / "test.html"
        _atomic_write(path, "<html>test</html>")
        assert path.read_text() == "<html>test</html>"

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "test.html"
        path.write_text("old content")
        _atomic_write(path, "new content")
        assert path.read_text() == "new content"

    def test_sets_permissions(self, tmp_path):
        import stat

        path = tmp_path / "perms.html"
        _atomic_write(path, "content")
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o644
