"""Pin the cross-link anchors emitted by ``_build_placeholder_page`` + the nav.

These guard the entry-point UX from state landing pages
(``Oregon.html`` / ``Montana.html`` / etc.) into the filtered all-states
views (``gauges.html#st=<State>`` etc.) and the pre-filtered pickers
(``picker.php?state=<State>``, ``gauge_picker.php?state=<State>``).
"""

from __future__ import annotations

import pytest

from kayak.dataset import region as region_mod
from kayak.web.build.shell import _build_nav, _build_placeholder_page

# The states with visible reaches in the live DB — what all_state_names() returns
# and what S3b-2 drives nav/landing pages off of (no hardcoded allowlist).
_STATES = ["California", "Idaho", "Montana", "Nevada", "Oregon", "Washington"]
_TEST_STATE_LABELS = {
    "California": "CA",
    "Idaho": "ID",
    "Montana": "MT",
    "Nevada": "NV",
    "Oregon": "OR",
    "Washington": "WA",
    "Wyoming": "WY",
    "New Mexico": "NM",
}


@pytest.fixture(autouse=True)
def _isolated_region_config(tmp_path, monkeypatch):
    """Keep nav tests independent from the operator's configured dataset."""
    monkeypatch.setattr("kayak.config.DATASET_DIR", tmp_path)
    region_mod.get_region_config.cache_clear()
    yield tmp_path
    region_mod.get_region_config.cache_clear()


@pytest.mark.parametrize("state", _STATES)
def test_landing_page_has_gauge_cross_links(state: str) -> None:
    """Every state landing page links to the filtered gauges view + gauge picker."""
    html = _build_placeholder_page("", _STATES, state, _TEST_STATE_LABELS)
    assert f'href="/gauges.html#st={state}"' in html, f"{state}.html missing gauges fragment link"
    assert f'href="/gauge_picker.php?state={state}"' in html, (
        f"{state}.html missing gauge picker link"
    )


@pytest.mark.parametrize("state", _STATES)
def test_landing_page_has_reach_cross_links_when_state_has_reaches(state: str) -> None:
    """States with reaches get the reaches + reach-picker anchors."""
    html = _build_placeholder_page("", _STATES, state, _TEST_STATE_LABELS)
    assert f'href="/index.html#st={state}"' in html, f"{state}.html missing reaches fragment link"
    assert f'href="/picker.php?state={state}"' in html, f"{state}.html missing reach picker link"


def test_landing_omits_reach_cross_links_for_non_reach_state() -> None:
    """A state not in the reach-states list has its body reach + reach-picker
    cross-link anchors suppressed; the gauge-side anchors still render. The build
    only emits pages for reach-states, so this exercises the defensive suppression
    in _build_placeholder_page directly."""
    html = _build_placeholder_page("", _STATES, "Wyoming", _TEST_STATE_LABELS)
    assert "→ Reaches in Wyoming" not in html
    assert "→ Reach picker — Wyoming" not in html
    # Gauge-side cross-links still present.
    assert "→ Live Wyoming gauges" in html
    assert "→ Gauge picker — Wyoming" in html
    assert "/gauges.html#st=Wyoming" in html
    assert "/gauge_picker.php?state=Wyoming" in html


def test_nav_bar_reach_picker_carries_active_state() -> None:
    """The nav-bar Reach Picker link picks up ?state=<active_state>."""
    html = _build_nav(
        _STATES, active_state="Oregon", picker_kind="reach", state_abbrevs=_TEST_STATE_LABELS
    )
    assert 'href="/picker.php?state=Oregon"' in html


def test_nav_bar_gauge_picker_carries_active_state() -> None:
    """Symmetric: the nav-bar Gauge Picker link carries the active state too."""
    html = _build_nav(
        _STATES, active_state="Montana", picker_kind="gauge", state_abbrevs=_TEST_STATE_LABELS
    )
    assert 'href="/gauge_picker.php?state=Montana"' in html


def test_nav_bar_picker_omits_state_when_no_active_state() -> None:
    """All-states pages (no active_state) get the bare picker URL."""
    html = _build_nav(
        _STATES,
        active_state="",
        picker_kind="reach",
        state_abbrevs=_TEST_STATE_LABELS,
    )
    assert 'href="/picker.php"' in html
    assert "?state=" not in html.split('href="/picker.php"')[1].split("</a>")[0]


def test_nav_bar_states_are_reaches_union_region_config(tmp_path) -> None:
    """S3b-2: nav buttons = reach-states (the passed `states`) plus the dataset region
    config's states. The generic engine default has no states, so seed an explicit
    region.yaml fixture instead of depending on local/live config."""
    (tmp_path / region_mod.REGION_YAML).write_text(
        "states:\n  Washington:\n    links: []\n",
        encoding="utf-8",
    )
    region_mod.get_region_config.cache_clear()
    html = _build_nav(["Oregon"], active_state="Oregon", state_abbrevs=_TEST_STATE_LABELS)
    for st in ("Washington", "Oregon"):
        assert f'href="/{st}.html"' in html, f"missing nav button for {st}"
    assert 'href="/Wyoming.html"' not in html  # in neither set → no button


def test_nav_bar_includes_reach_state_absent_from_region_config() -> None:
    """A state with reaches but not in the region config still gets a button (union),
    so a state with content never silently vanishes from nav."""
    html = _build_nav(["Wyoming"], state_abbrevs=_TEST_STATE_LABELS)
    assert 'href="/Wyoming.html"' in html and ">WY</a>" in html


def test_region_urls_with_query_strings_escape_in_rendered_html(tmp_path) -> None:
    """A legitimate `&` in a region weather/link URL must render as `&amp;` in
    the emitted href attributes (the carried S3b Dreamflows finding). Guarded at
    the HTML layer so a renderer refactor can't reintroduce raw ampersands while
    the region-model tests (which only check the parsed URL) stay green."""
    (tmp_path / region_mod.REGION_YAML).write_text(
        "states:\n"
        "  Oregon:\n"
        "    weather_url: 'https://wx.example/f?a=1&b=2'\n"
        "    links:\n"
        "      - {label: DF, url: 'https://x.example/f?a=1&b=2#frag'}\n",
        encoding="utf-8",
    )
    region_mod.get_region_config.cache_clear()
    nav = _build_nav(_STATES, active_state="Oregon", state_abbrevs=_TEST_STATE_LABELS)
    assert 'href="https://wx.example/f?a=1&amp;b=2"' in nav
    assert 'href="https://wx.example/f?a=1&b=2"' not in nav
    page = _build_placeholder_page("", _STATES, "Oregon", _TEST_STATE_LABELS)
    assert 'href="https://x.example/f?a=1&amp;b=2#frag"' in page
    assert 'href="https://x.example/f?a=1&b=2#frag"' not in page


def test_nav_bar_percent_encodes_multiword_state_url() -> None:
    """A multi-word state name is percent-encoded in the nav href path (review)."""
    html = _build_nav(["New Mexico"], state_abbrevs=_TEST_STATE_LABELS)
    assert 'href="/New%20Mexico.html"' in html
    assert 'href="/New Mexico.html"' not in html  # never a raw space in the URL
