"""Tests for kayak.db.info_db query helpers."""

from kayak.db.info_db import (
    all_state_names,
    all_states,
    classify_level,
    display_name,
    get_all_primary_source_ids,
    get_calculated_source_ids,
    get_gauge_for_reach,
    get_primary_source_id,
    get_reach,
    get_reach_by_name,
    get_source_ids_for_gauge,
    reaches_query,
)
from kayak.db.models import (
    CalcExpression,
    DataType,
    FetchUrl,
    FlowLevel,
    Gauge,
    GaugeSource,
    Reach,
    ReachLevel,
    ReachState,
    Source,
    State,
)

# ---------------------------------------------------------------------------
# all_states / all_state_names
# ---------------------------------------------------------------------------


def test_all_states_empty(session):
    """all_states returns empty list when no State records exist."""
    assert all_states(session) == []


def test_all_states_sorted(session):
    """all_states returns State records sorted alphabetically by name."""
    session.add_all(
        [
            State(name="WA", abbreviation="WA"),
            State(name="ID", abbreviation="ID"),
            State(name="OR", abbreviation="OR"),
        ]
    )
    session.flush()

    result = all_states(session)
    names = [s.name for s in result]
    assert names == ["ID", "OR", "WA"]


def test_all_state_names_sorted(session):
    """all_state_names returns sorted names of states that have visible reaches."""
    states = [
        State(name="WA", abbreviation="WA"),
        State(name="ID", abbreviation="ID"),
        State(name="OR", abbreviation="OR"),
    ]
    session.add_all(states)
    session.flush()

    # Link a visible reach to each state
    for st in states:
        r = Reach(
            name=f"r_{st.name}", display_name=f"River {st.name}", sort_name=f"River {st.name}"
        )
        session.add(r)
        session.flush()
        session.add(ReachState(reach_id=r.id, state_id=st.id))
    session.flush()

    result = all_state_names(session)
    assert result == ["ID", "OR", "WA"]


# ---------------------------------------------------------------------------
# reaches_query
# ---------------------------------------------------------------------------


def _add_reach(session, name, sort_name, *, no_show=False, gauge_id=None):
    """Helper to add a reach with minimal required fields."""
    reach = Reach(
        name=name,
        display_name=name,
        sort_name=sort_name,
        no_show=no_show,
        gauge_id=gauge_id,
    )
    session.add(reach)
    session.flush()
    return reach


def test_reaches_query_sorted(session):
    """reaches_query returns reaches sorted by sort_name."""
    _add_reach(session, "z_river", "Zeta River")
    _add_reach(session, "a_river", "Alpha River")
    _add_reach(session, "m_river", "Mu River")

    result = reaches_query(session, visible_only=False)
    names = [r.sort_name for r in result]
    assert names == ["Alpha River", "Mu River", "Zeta River"]


def test_reaches_query_visible_only_excludes_no_show(session):
    """reaches_query with visible_only=True filters out no_show reaches."""
    _add_reach(session, "visible", "A Visible")
    _add_reach(session, "hidden", "B Hidden", no_show=True)

    result = reaches_query(session, visible_only=True)
    assert len(result) == 1
    assert result[0].name == "visible"


def test_reaches_query_state_filter(session):
    """reaches_query filters by state name through ReachState junction."""
    state_or = State(name="OR", abbreviation="OR")
    state_wa = State(name="WA", abbreviation="WA")
    session.add_all([state_or, state_wa])
    session.flush()

    reach_or = _add_reach(session, "deschutes", "Deschutes")
    reach_wa = _add_reach(session, "skagit", "Skagit")
    session.add(ReachState(reach_id=reach_or.id, state_id=state_or.id))
    session.add(ReachState(reach_id=reach_wa.id, state_id=state_wa.id))
    session.flush()

    result = reaches_query(session, state_name="OR", visible_only=False)
    assert len(result) == 1
    assert result[0].name == "deschutes"


# ---------------------------------------------------------------------------
# get_reach / get_reach_by_name / display_name
# ---------------------------------------------------------------------------


def test_get_reach_valid_id(session, sample_reach):
    """get_reach returns the Reach for a valid ID."""
    result = get_reach(session, sample_reach.id)
    assert result is not None
    assert result.id == sample_reach.id


def test_get_reach_invalid_id(session):
    """get_reach returns None for a nonexistent ID."""
    assert get_reach(session, 99999) is None


def test_get_reach_by_name(session, sample_reach):
    """get_reach_by_name returns the Reach matching the unique name."""
    result = get_reach_by_name(session, sample_reach.name)
    assert result is not None
    assert result.id == sample_reach.id


def test_display_name(session, sample_reach):
    """display_name returns the display_name string for a reach ID."""
    result = display_name(session, sample_reach.id)
    assert result == "Test River - Upper"


# ---------------------------------------------------------------------------
# get_gauge_for_reach
# ---------------------------------------------------------------------------


def test_get_gauge_for_reach_linked(session, sample_reach, sample_gauge):
    """get_gauge_for_reach returns the linked Gauge."""
    result = get_gauge_for_reach(session, sample_reach.id)
    assert result is not None
    assert result.id == sample_gauge.id


def test_get_gauge_for_reach_no_gauge(session):
    """get_gauge_for_reach returns None when reach has no gauge."""
    reach = Reach(name="no_gauge", display_name="No Gauge", sort_name="No Gauge")
    session.add(reach)
    session.flush()

    assert get_gauge_for_reach(session, reach.id) is None


# ---------------------------------------------------------------------------
# get_primary_source_id / get_source_ids_for_gauge
# ---------------------------------------------------------------------------


def _make_source(session, name="test"):
    """Helper to create a Source with a FetchUrl."""
    fu = FetchUrl(url=f"https://example.com/{name}", parser="usgs", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


def test_get_primary_source_id_linked(session, sample_gauge):
    """get_primary_source_id returns the source_id when a GaugeSource link exists."""
    src = _make_source(session, "primary")
    session.add(GaugeSource(gauge_id=sample_gauge.id, source_id=src.id))
    session.flush()

    result = get_primary_source_id(session, sample_gauge.id)
    assert result == src.id


def test_get_primary_source_id_no_link(session, sample_gauge):
    """get_primary_source_id returns None when no GaugeSource link exists."""
    assert get_primary_source_id(session, sample_gauge.id) is None


def test_get_source_ids_for_gauge_multiple(session, sample_gauge):
    """get_source_ids_for_gauge returns all linked source IDs."""
    src_a = _make_source(session, "src_a")
    src_b = _make_source(session, "src_b")
    session.add(GaugeSource(gauge_id=sample_gauge.id, source_id=src_a.id))
    session.add(GaugeSource(gauge_id=sample_gauge.id, source_id=src_b.id))
    session.flush()

    result = get_source_ids_for_gauge(session, sample_gauge.id)
    assert sorted(result) == sorted([src_a.id, src_b.id])


# ---------------------------------------------------------------------------
# get_all_primary_source_ids / get_calculated_source_ids
# ---------------------------------------------------------------------------


def test_get_all_primary_source_ids(session):
    """get_all_primary_source_ids maps multiple gauge_ids to source_ids."""
    g1 = Gauge(name="gauge1", usgs_id="111")
    g2 = Gauge(name="gauge2", usgs_id="222")
    session.add_all([g1, g2])
    session.flush()

    s1 = _make_source(session, "src1")
    s2 = _make_source(session, "src2")
    session.add(GaugeSource(gauge_id=g1.id, source_id=s1.id))
    session.add(GaugeSource(gauge_id=g2.id, source_id=s2.id))
    session.flush()

    result = get_all_primary_source_ids(session, [g1.id, g2.id])
    assert result[g1.id] == s1.id
    assert result[g2.id] == s2.id


def test_get_all_primary_source_ids_empty(session):
    """Empty gauge_ids returns empty dict."""
    assert get_all_primary_source_ids(session, []) == {}


def test_get_calculated_source_ids(session):
    """get_calculated_source_ids returns only sources with calc_expression."""
    ce = CalcExpression(data_type=DataType.flow, expression="A + B")
    session.add(ce)
    session.flush()

    # Source with fetch_url (not calculated)
    s_fetch = _make_source(session, "fetch_src")
    # Source with calc_expression (calculated)
    s_calc = Source(name="calc_src", calc_expression_id=ce.id)
    session.add(s_calc)
    session.flush()

    result = get_calculated_source_ids(session, [s_fetch.id, s_calc.id])
    assert s_calc.id in result
    assert s_fetch.id not in result


def test_get_calculated_source_ids_empty(session):
    """Empty source_ids returns empty set."""
    assert get_calculated_source_ids(session, []) == set()


# ---------------------------------------------------------------------------
# classify_level
# ---------------------------------------------------------------------------


class TestClassifyLevel:
    def _make_reach_with_levels(self, session):
        gauge = Gauge(name="clf_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="clf_r", gauge_id=gauge.id)
        session.add(reach)
        session.flush()
        levels = [
            ReachLevel(
                reach_id=reach.id,
                level=FlowLevel.low,
                low=0.0,
                low_data_type=DataType.flow,
                high=500.0,
                high_data_type=DataType.flow,
            ),
            ReachLevel(
                reach_id=reach.id,
                level=FlowLevel.okay,
                low=500.0,
                low_data_type=DataType.flow,
                high=2000.0,
                high_data_type=DataType.flow,
            ),
            ReachLevel(
                reach_id=reach.id,
                level=FlowLevel.high,
                low=2000.0,
                low_data_type=DataType.flow,
                high=5000.0,
                high_data_type=DataType.flow,
            ),
        ]
        session.add_all(levels)
        session.flush()
        return reach

    def test_classify_flow_okay(self, session):
        reach = self._make_reach_with_levels(session)
        assert classify_level(reach, DataType.flow, 1000.0) == FlowLevel.okay

    def test_classify_flow_low(self, session):
        reach = self._make_reach_with_levels(session)
        assert classify_level(reach, DataType.flow, 100.0) == FlowLevel.low

    def test_classify_flow_high(self, session):
        reach = self._make_reach_with_levels(session)
        assert classify_level(reach, DataType.flow, 3000.0) == FlowLevel.high

    def test_classify_boundary_inclusive(self, session):
        reach = self._make_reach_with_levels(session)
        # 500 is at the boundary of low [0, 500] and okay [500, 2000]
        result = classify_level(reach, DataType.flow, 500.0)
        assert result in (FlowLevel.low, FlowLevel.okay)

    def test_classify_no_levels_returns_none(self, session):
        gauge = Gauge(name="nolvl_g")
        session.add(gauge)
        session.flush()
        reach = Reach(name="nolvl_r", gauge_id=gauge.id)
        session.add(reach)
        session.flush()
        assert classify_level(reach, DataType.flow, 1000.0) is None

    def test_classify_wrong_data_type_returns_none(self, session):
        reach = self._make_reach_with_levels(session)
        # All levels are for DataType.flow, querying with gauge should not match
        assert classify_level(reach, DataType.gauge, 1000.0) is None
