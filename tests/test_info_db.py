"""Tests for kayak.db.info_db query helpers."""

from kayak.db.info_db import (
    all_state_names,
    all_states,
    display_name,
    get_gauge_for_section,
    get_primary_source_id,
    get_section,
    get_section_by_name,
    get_source_ids_for_gauge,
    sections_query,
)
from kayak.db.models import FetchUrl, GaugeSource, Section, SectionState, Source, State

# ---------------------------------------------------------------------------
# all_states / all_state_names
# ---------------------------------------------------------------------------


def test_all_states_empty(session):
    """all_states returns empty list when no State records exist."""
    assert all_states(session) == []


def test_all_states_sorted(session):
    """all_states returns State records sorted alphabetically by name."""
    session.add_all([
        State(name="WA", abbreviation="WA"),
        State(name="ID", abbreviation="ID"),
        State(name="OR", abbreviation="OR"),
    ])
    session.flush()

    result = all_states(session)
    names = [s.name for s in result]
    assert names == ["ID", "OR", "WA"]


def test_all_state_names_sorted(session):
    """all_state_names returns a sorted list of name strings."""
    session.add_all([
        State(name="WA", abbreviation="WA"),
        State(name="ID", abbreviation="ID"),
        State(name="OR", abbreviation="OR"),
    ])
    session.flush()

    result = all_state_names(session)
    assert result == ["ID", "OR", "WA"]


# ---------------------------------------------------------------------------
# sections_query
# ---------------------------------------------------------------------------


def _add_section(session, name, sort_name, *, no_show=False, gauge_id=None):
    """Helper to add a section with minimal required fields."""
    sec = Section(
        name=name,
        display_name=name,
        sort_name=sort_name,
        no_show=no_show,
        gauge_id=gauge_id,
    )
    session.add(sec)
    session.flush()
    return sec


def test_sections_query_sorted(session):
    """sections_query returns sections sorted by sort_name."""
    _add_section(session, "z_river", "Zeta River")
    _add_section(session, "a_river", "Alpha River")
    _add_section(session, "m_river", "Mu River")

    result = sections_query(session, visible_only=False)
    names = [s.sort_name for s in result]
    assert names == ["Alpha River", "Mu River", "Zeta River"]


def test_sections_query_visible_only_excludes_no_show(session):
    """sections_query with visible_only=True filters out no_show sections."""
    _add_section(session, "visible", "A Visible")
    _add_section(session, "hidden", "B Hidden", no_show=True)

    result = sections_query(session, visible_only=True)
    assert len(result) == 1
    assert result[0].name == "visible"


def test_sections_query_state_filter(session):
    """sections_query filters by state name through SectionState junction."""
    state_or = State(name="OR", abbreviation="OR")
    state_wa = State(name="WA", abbreviation="WA")
    session.add_all([state_or, state_wa])
    session.flush()

    sec_or = _add_section(session, "deschutes", "Deschutes")
    sec_wa = _add_section(session, "skagit", "Skagit")
    session.add(SectionState(section_id=sec_or.id, state_id=state_or.id))
    session.add(SectionState(section_id=sec_wa.id, state_id=state_wa.id))
    session.flush()

    result = sections_query(session, state_name="OR", visible_only=False)
    assert len(result) == 1
    assert result[0].name == "deschutes"


# ---------------------------------------------------------------------------
# get_section / get_section_by_name / display_name
# ---------------------------------------------------------------------------


def test_get_section_valid_id(session, sample_section):
    """get_section returns the Section for a valid ID."""
    result = get_section(session, sample_section.id)
    assert result is not None
    assert result.id == sample_section.id


def test_get_section_invalid_id(session):
    """get_section returns None for a nonexistent ID."""
    assert get_section(session, 99999) is None


def test_get_section_by_name(session, sample_section):
    """get_section_by_name returns the Section matching the unique name."""
    result = get_section_by_name(session, sample_section.name)
    assert result is not None
    assert result.id == sample_section.id


def test_display_name(session, sample_section):
    """display_name returns the display_name string for a section ID."""
    result = display_name(session, sample_section.id)
    assert result == "Test River - Upper"


# ---------------------------------------------------------------------------
# get_gauge_for_section
# ---------------------------------------------------------------------------


def test_get_gauge_for_section_linked(session, sample_section, sample_gauge):
    """get_gauge_for_section returns the linked Gauge."""
    result = get_gauge_for_section(session, sample_section.id)
    assert result is not None
    assert result.id == sample_gauge.id


def test_get_gauge_for_section_no_gauge(session):
    """get_gauge_for_section returns None when section has no gauge."""
    sec = Section(name="no_gauge", display_name="No Gauge", sort_name="No Gauge")
    session.add(sec)
    session.flush()

    assert get_gauge_for_section(session, sec.id) is None


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
