"""Tests for the kayak.web.build output generators."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from kayak.dataset import region as region_mod
from kayak.dataset import site as site_mod
from kayak.db.models import (
    DataType,
    FetchUrl,
    Gauge,
    GaugeSource,
    LatestGaugeObservation,
    Observation,
    Reach,
    ReachClass,
    Source,
    State,
)
from kayak.web.build._shared import _atomic_write
from kayak.web.build.levels import _build_html_table, _get_row_data, _levels_key
from kayak.web.build.shell import (
    _build_letter_nav,
    _build_map_page,
    _build_nav,
    _build_page,
    _build_right_cluster,
)
from kayak.web.build.sparklines import _build_sparkline, _select_sparkline_series

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

STATE_ABBREVS = {"Oregon": "OR", "Washington": "WA", "Wyoming": "WY"}


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

    def test_state_cell_uses_dataset_abbreviation(self, session):
        state = State(name="Atlantis", abbreviation="ZZ")
        reach = Reach(name="custom_state", display_name="Custom State River")
        reach.states.append(state)
        session.add(reach)
        session.flush()

        row = _get_row_data(reach, set(), {})

        assert row["state"] == "ZZ"

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
        rc = ReachClass(
            reach_id=reach.id,
            name="III",
            low=500.0,
            low_data_type=DataType.flow,
            high=2000.0,
            high_data_type=DataType.flow,
        )
        session.add(rc)
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
# _select_sparkline_series — series-picking for sparkline fallback
# ---------------------------------------------------------------------------


def _seed_obs(session, gauge, data_type, hours_ago_list, base_value=100.0):
    """Attach observations at the given hours-ago offsets to a new source."""
    fu = FetchUrl(url=f"https://example.com/{gauge.name}-{data_type.value}", parser="test")
    session.add(fu)
    session.flush()
    src = Source(name=f"{gauge.name}-{data_type.value}", fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    session.add(GaugeSource(gauge_id=gauge.id, source_id=src.id))
    now = datetime.now(UTC)
    for i, h in enumerate(hours_ago_list):
        session.add(
            Observation(
                source_id=src.id,
                observed_at=now - timedelta(hours=h),
                data_type=data_type,
                value=base_value + i,
            )
        )
    session.flush()


class TestSelectSparklineSeries:
    def test_picks_flow_when_current(self, session):
        g = Gauge(name="G_flow_current")
        session.add(g)
        session.flush()
        _seed_obs(session, g, DataType.flow, [5, 2, 0.5])
        _seed_obs(session, g, DataType.gauge, [5, 2, 0.5], base_value=1.0)

        picked = _select_sparkline_series(session, [g.id])
        assert g.id in picked
        assert all(o.data_type == DataType.flow for o in picked[g.id])

    def test_falls_back_to_inflow_when_flow_stale(self, session):
        g = Gauge(name="G_inflow")
        session.add(g)
        session.flush()
        # flow only has stale points (latest 24h ago)
        _seed_obs(session, g, DataType.flow, [40, 30, 24])
        _seed_obs(session, g, DataType.inflow, [5, 2, 0.5])

        picked = _select_sparkline_series(session, [g.id])
        assert all(o.data_type == DataType.inflow for o in picked[g.id])

    def test_falls_back_to_gauge_when_flow_and_inflow_stale(self, session):
        g = Gauge(name="G_gauge_fallback")
        session.add(g)
        session.flush()
        _seed_obs(session, g, DataType.flow, [72, 60, 40])  # all >6h
        _seed_obs(session, g, DataType.inflow, [72, 60, 40])
        _seed_obs(session, g, DataType.gauge, [5, 2, 0.5])  # fresh

        picked = _select_sparkline_series(session, [g.id])
        assert all(o.data_type == DataType.gauge for o in picked[g.id])

    def test_no_series_when_all_stale(self, session):
        g = Gauge(name="G_all_stale")
        session.add(g)
        session.flush()
        _seed_obs(session, g, DataType.flow, [72, 60, 40])
        _seed_obs(session, g, DataType.gauge, [72, 60, 40])

        picked = _select_sparkline_series(session, [g.id])
        assert g.id not in picked

    def test_excludes_observations_outside_48h_window(self, session):
        """The 48h fetch window clips far-past data even if a current point exists."""
        g = Gauge(name="G_windowed")
        session.add(g)
        session.flush()
        # A current point plus very old ones — only the current makes the window.
        _seed_obs(session, g, DataType.flow, [200, 150, 0.5])

        picked = _select_sparkline_series(session, [g.id])
        # All picked points are within 48h (only the 0.5h one should survive).
        assert len(picked[g.id]) == 1


# ---------------------------------------------------------------------------
# _build_html_table
# ---------------------------------------------------------------------------


class TestBuildHTMLTable:
    def test_produces_table(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Clackamas", "flow": 900.0}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _letters = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "<table" in result
        assert "</table>" in result

    def test_includes_flow_value(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Sandy", "flow": 750.0}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "750" in result

    def test_name_link(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "White Salmon", "flow": 1200.0}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        from kayak.utils.pubhash import encode as pubhash_encode

        # Public links carry the base-62 handle of the (stable) reach id, not
        # the raw decimal id — the non-transitional Phase-2 surface.
        assert f"description.php?h={pubhash_encode(reaches[0].id)}" in result
        assert "description.php?id=" not in result
        assert "White Salmon" in result

    def test_expired_rows_filtered(self, session):
        reaches = _make_reaches(session, count=2)
        rows = [
            {"display_name": "Fresh River", "flow": 500.0},
            {"display_name": "Old River", "flow": 100.0, "expired": True},
        ]
        with (
            mock.patch("kayak.web.build.levels._get_row_data", side_effect=rows),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "Fresh River" in result
        assert "Old River" not in result

    def test_sparkline_slot_emitted_even_without_flow_value(self, session):
        """Rows with a gauge but no flow value still need the spark span so
        the gauge-height fallback sparkline has somewhere to land."""
        reaches = _make_reaches(session, count=1)
        gauge = Gauge(name="no_flow_gauge")
        session.add(gauge)
        session.flush()
        reaches[0].gauge_id = gauge.id
        session.flush()
        # Row has gage/temperature but no flow value — the fallback case.
        fake_row = {"display_name": "GaugeOnly River", "gage": 3.4, "temperature": 45.7}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "GaugeOnly River" in result
        # The spark placeholder must carry data-gid=<gauge.id> so levels.js
        # can inject the pre-built gauge-height SVG from sparklines.json.
        assert f'class="spark" data-gid="{gauge.id}"' in result

    def test_empty_data_filtered(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Empty River"}  # no flow/gage/temperature
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "Empty River" not in result

    def test_stale_class_applied(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Stale River", "flow": 200.0, "stale": True}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
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
            mock.patch("kayak.web.build.levels._get_row_data", side_effect=rows),
        ):
            _, letters = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "A" in letters
        assert "B" in letters
        assert "G" in letters

    def test_no_rowspan_on_shared_gauge(self, session):
        """Each reach now renders its own gauge cells; rowspan was removed so
        filtering can hide individual rows without leaving spanned cells
        orphaned."""
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
            mock.patch("kayak.web.build.levels._get_row_data", side_effect=rows),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "rowspan=" not in result
        # Each row gets its own flow cell.
        assert result.count('class="td-flow"') == 2

    def test_status_column_rendering(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Status River", "flow": 100.0, "status": "high"}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
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
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_data_label_html_escaped(self, session):
        evil_col = {**COLS_SIMPLE[1], "name_text": 'Flow"onmouseover="alert(1)'}
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Test", "flow": 100.0}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
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
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS_SIMPLE, set(), {})
        assert "(est)" in result

    def test_gage_value_rendering(self, session):
        reaches = _make_reaches(session, count=1)
        fake_row = {"display_name": "Gage River", "gage": 4.25}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "4.2" in result  # gage renders as .1f

    def test_time_column_rendering(self, session):
        reaches = _make_reaches(session, count=1)
        dt = datetime(2026, 4, 10, 14, 30, tzinfo=UTC)
        fake_row = {"display_name": "Time River", "flow": 100.0, "time": dt}
        with (
            mock.patch("kayak.web.build.levels._get_row_data", return_value=fake_row),
        ):
            result, _ = _build_html_table(reaches, COLS, set(), {})
        assert "<time" in result
        assert "04/10 14:30" in result


# Coverage for the split geojson builders lives in
# tests/test_build_geojson_split.py.


# ---------------------------------------------------------------------------
# Page construction helpers
# ---------------------------------------------------------------------------


class TestBuildNav:
    def test_includes_map_and_picker(self, session):
        result = _build_nav(["Oregon", "Washington"], state_abbrevs=STATE_ABBREVS)
        assert "map.html" in result
        assert "picker.php" in result

    def test_active_state_highlighted(self, session):
        result = _build_nav(
            ["Oregon", "Washington"],
            active_state="Oregon",
            state_abbrevs=STATE_ABBREVS,
        )
        assert 'class="active"' in result

    def test_nav_labels_use_dataset_abbreviations(self, session):
        result = _build_nav(["Oregon"], state_abbrevs={"Oregon": "XX"})
        assert ">XX</a>" in result
        assert ">OR</a>" not in result

    def test_nav_buttons_are_reaches_union_region(self, session, tmp_path, monkeypatch):
        # S3b-2: nav buttons = passed reach-states + dataset region config states,
        # not a hardcoded allowlist or generic engine default.
        (tmp_path / region_mod.REGION_YAML).write_text(
            "states:\n  Washington:\n    links: []\n  Oregon:\n    links: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("kayak.config.DATASET_DIR", tmp_path)
        region_mod.get_region_config.cache_clear()
        try:
            result = _build_nav(["Oregon"], state_abbrevs=STATE_ABBREVS)
        finally:
            region_mod.get_region_config.cache_clear()
        assert 'href="/Oregon.html"' in result
        assert 'href="/Washington.html"' in result
        assert 'href="/Wyoming.html"' not in result  # in neither set → no button

    def test_right_cluster_uses_dataset_org_identity(self, tmp_path, monkeypatch):
        (tmp_path / site_mod.SITE_YAML).write_text(
            "org_name: Foo Paddlers\norg_url: https://foo.example\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("kayak.config.DATASET_DIR", tmp_path)
        site_mod.get_site_config.cache_clear()
        try:
            result = _build_right_cluster()
        finally:
            site_mod.get_site_config.cache_clear()
        assert 'href="https://foo.example"' in result
        assert ">FP</a>" in result


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


class TestBuildMapPage:
    def test_embeds_split_urls(self):
        result = _build_map_page(
            "",
            ["Oregon"],
            "/static/reaches-geom.json?v=abc123",
            "/static/reaches-state.json",
            site_config_url="/static/site-config.json?v=def456",
        )
        assert 'id="map"' in result
        assert 'data-geom-url="/static/reaches-geom.json?v=abc123"' in result
        assert 'data-state-url="/static/reaches-state.json"' in result
        # S3d: the map config URL is wired; legacy per-layer data-osmb-* attrs are gone
        # (the layer URLs now live inside site-config.json).
        assert 'data-site-config-url="/static/site-config.json?v=def456"' in result
        assert "data-osmb-" not in result


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
        rc = ReachClass(
            reach_id=reach.id,
            name="III",
            low=500.0,
            low_data_type=DataType.flow,
            high=2000.0,
            high_data_type=DataType.flow,
        )
        session.add(rc)
        session.flush()
        key = _levels_key(reach)
        assert key != ()
        assert len(key) == 4


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


class TestDeployStaticAssets:
    """S4a-2 slice B1: committed assets are copied from the *packaged*
    web/static dir (so a wheel install finds them), and map overlay GeoJSON
    staged outside the package via ``config.map_layers_dir`` flows through the
    build output — which is what puts it in the build's ``kept`` set so
    ``_sweep_orphans`` preserves it. A break here silently drops overlay layers
    from the live map (the file would not be re-staged and the next build would
    sweep it).
    """

    @staticmethod
    def _map_config_for_overlay_files(*filenames: str):
        from kayak.dataset.map import MapConfig, MapLayer

        return MapConfig(
            layers=[
                MapLayer(
                    key=f"layer{i}",
                    label=f"Layer {i}",
                    color="#abcdef",
                    shape="circle",
                    size=5,
                    popup="access",
                    popup_link="https://example.com/layer",
                    output_filename=filename,
                    endpoint="https://services.example.com/FeatureServer/0",
                    out_fields=["name"],
                )
                for i, filename in enumerate(filenames)
            ]
        )

    def test_copies_packaged_assets_and_staged_map_layers(self, tmp_path):
        from kayak.web.build import deploy

        map_layers_dir = tmp_path / "map-layers"
        map_layers_dir.mkdir()
        (map_layers_dir / "hazards.geojson").write_text(
            '{"type":"FeatureCollection","features":[]}'
        )
        output = tmp_path / "out"

        with (
            mock.patch.object(deploy, "MAP_LAYERS_DIR", map_layers_dir),
            mock.patch.object(
                deploy,
                "get_map_config",
                return_value=self._map_config_for_overlay_files("hazards.geojson"),
            ),
            mock.patch.object(deploy, "_deploy_regression_artifacts"),
        ):
            deploy._deploy_static_assets(output)

        static = output / "static"
        # Committed source assets resolved from the package.
        assert (static / "map.js").is_file()
        assert (static / "leaflet.js").is_file()
        assert (static / "images" / "marker-icon.png").is_file()
        # sw.js lands at the output root so the service worker controls scope "/".
        assert (output / "sw.js").is_file()
        assert not (static / "sw.js").exists()
        # Map overlay staged outside the package reaches the build output.
        assert (static / "hazards.geojson").is_file()
        # The build-processed trio is NOT copied as-is here (emitted as the
        # hashed/versioned variants by _build_to_dir).
        for name in deploy._BUILD_PROCESSED_STATIC:
            assert not (static / name).exists(), name

    def test_skips_stale_unconfigured_map_layer_files(self, tmp_path):
        from kayak.web.build import deploy

        map_layers_dir = tmp_path / "map-layers"
        map_layers_dir.mkdir()
        (map_layers_dir / "configured.geojson").write_text(
            '{"type":"FeatureCollection","features":[]}'
        )
        (map_layers_dir / "stale.geojson").write_text('{"type":"FeatureCollection","features":[]}')
        output = tmp_path / "out"

        with (
            mock.patch.object(deploy, "MAP_LAYERS_DIR", map_layers_dir),
            mock.patch.object(
                deploy,
                "get_map_config",
                return_value=self._map_config_for_overlay_files("configured.geojson"),
            ),
            mock.patch.object(deploy, "_deploy_regression_artifacts"),
        ):
            deploy._deploy_static_assets(output)

        assert (output / "static" / "configured.geojson").is_file()
        assert not (output / "static" / "stale.geojson").exists()

    def test_empty_map_config_skips_all_staged_map_layer_files(self, tmp_path):
        from kayak.web.build import deploy

        map_layers_dir = tmp_path / "map-layers"
        map_layers_dir.mkdir()
        (map_layers_dir / "stale.geojson").write_text('{"type":"FeatureCollection","features":[]}')
        output = tmp_path / "out"

        with (
            mock.patch.object(deploy, "MAP_LAYERS_DIR", map_layers_dir),
            mock.patch.object(
                deploy, "get_map_config", return_value=self._map_config_for_overlay_files()
            ),
            mock.patch.object(deploy, "_deploy_regression_artifacts"),
        ):
            deploy._deploy_static_assets(output)

        assert not list((output / "static").glob("*.geojson"))

    def test_missing_map_layers_dir_is_tolerated(self, tmp_path):
        from kayak.web.build import deploy

        output = tmp_path / "out"
        with (
            mock.patch.object(deploy, "MAP_LAYERS_DIR", tmp_path / "does-not-exist"),
            mock.patch.object(deploy, "_deploy_regression_artifacts"),
        ):
            deploy._deploy_static_assets(output)
        assert (output / "static" / "map.js").is_file()
        assert not list((output / "static").glob("*.geojson"))


class TestDeployPhpConfigLicense:
    """S4a-2 slice B2: the PHP layer, install templates, and LICENSE files ship
    inside the package (``src/kayak/web/{php,install-templates,legal}``) and the
    deploy helpers resolve them via the package, not a repo-root ``BASE_DIR``
    path — so a wheel install can render the site."""

    def test_deploy_php_files(self, tmp_path):
        from kayak.web.build import deploy

        out = tmp_path / "out"
        out.mkdir()
        deploy._deploy_php_files(out)
        assert (out / "latest.php").is_file()  # a top-level page
        assert (out / "includes" / "db.php").is_file()  # includes/
        assert (out / "includes" / "states.php").is_file()  # state-reference helper
        assert (out / "_internal" / "index.php").is_file()  # maintainer dashboard
        assert (out / "style.css").is_file()  # css at output root for php header

    def test_deploy_config_files(self, tmp_path, monkeypatch):
        from kayak.web.build import deploy

        out = tmp_path / "out"
        out.mkdir()
        monkeypatch.setattr(deploy, "SITE_URL", "https://example.test/base/")
        deploy._deploy_config_files(out)
        assert (out / "404.html").is_file()
        assert (out / "robots.txt").is_file()
        robots = (out / "robots.txt").read_text(encoding="utf-8")
        assert "Sitemap: https://example.test/base/sitemap.xml" in robots
        assert "__SITE_URL__" not in robots

    def test_deploy_config_files_rejects_unparameterized_robots(self, tmp_path, monkeypatch):
        from kayak.web.build import deploy

        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "robots.txt").write_text(
            "User-agent: *\nAllow: /\n\nSitemap: https://example.test/sitemap.xml\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(deploy, "resource_dir", lambda *parts: templates)

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(RuntimeError, match=r"robots\.txt.*__SITE_URL__"):
            deploy._deploy_config_files(out)

    def test_deploy_license_files(self, tmp_path):
        from kayak.web.build import deploy

        out = tmp_path / "out"
        out.mkdir()
        deploy._deploy_license_files(out)
        assert (out / "LICENSE.txt").is_file()
        assert (out / "LICENSE-DATA.txt").is_file()

    def test_deploy_license_files_prefers_dataset_data_license(self, tmp_path, monkeypatch):
        from kayak.web.build import deploy

        ds = tmp_path / "ds"
        ds.mkdir()
        (ds / "LICENSE-DATA.txt").write_text("Dataset-specific data license\n", encoding="utf-8")
        monkeypatch.setattr(deploy, "DATASET_DIR", ds)

        out = tmp_path / "out"
        out.mkdir()
        deploy._deploy_license_files(out)

        assert (out / "LICENSE-DATA.txt").read_text(encoding="utf-8") == (
            "Dataset-specific data license\n"
        )

    def test_deploy_license_files_rejects_non_file_dataset_license(self, tmp_path, monkeypatch):
        from kayak.web.build import deploy

        ds = tmp_path / "ds"
        (ds / "LICENSE-DATA.txt").mkdir(parents=True)
        monkeypatch.setattr(deploy, "DATASET_DIR", ds)

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(RuntimeError, match=r"LICENSE-DATA\.txt.*regular file"):
            deploy._deploy_license_files(out)

    def test_packaged_data_license_fallback_is_generic(self):
        from kayak.resources import resource_dir

        text = (resource_dir("web", "legal") / "LICENSE-DATA.txt").read_text(encoding="utf-8")
        assert "levels.wkcc.org" not in text
        assert "Willamette Kayak" not in text

    def test_packaged_robots_template_is_site_url_parameterized(self):
        from kayak.resources import resource_dir

        text = (resource_dir("web", "install-templates") / "robots.txt").read_text(encoding="utf-8")
        assert "Sitemap: __SITE_URL__/sitemap.xml" in text
        assert "levels.wkcc.org" not in text

    def test_packaged_php_prose_fallbacks_are_generic(self):
        from kayak.resources import resource_dir

        php = resource_dir("web", "php")
        forbidden = (
            "levels.wkcc.org",
            "Willamette Kayak",
            "WKCC",
            "Pat Welch",
            "Soggy Sneakers",
        )
        for page in ("about.php", "privacy.php", "disclaimer.php"):
            text = (php / page).read_text(encoding="utf-8")
            for needle in forbidden:
                assert needle not in text

    def test_packaged_license_matches_repo_root(self):
        """The repo-root LICENSE/LICENSE-DATA (GitHub + pyproject convention)
        and the packaged copies the deploy serves must not drift."""
        from kayak.resources import resource_dir

        repo_root = Path(__file__).resolve().parents[2]
        legal = resource_dir("web", "legal")
        assert (repo_root / "LICENSE").read_bytes() == (legal / "LICENSE.txt").read_bytes()
        assert (repo_root / "LICENSE-DATA").read_bytes() == (
            legal / "LICENSE-DATA.txt"
        ).read_bytes()
