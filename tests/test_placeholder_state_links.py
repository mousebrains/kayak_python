"""Pin the cross-link anchors emitted by ``_build_placeholder_page`` + the nav.

These guard the entry-point UX from state landing pages
(``Oregon.html`` / ``Montana.html`` / etc.) into the filtered all-states
views (``gauges.html#st=<State>`` etc.) and the pre-filtered pickers
(``picker.php?state=<State>``, ``gauge_picker.php?state=<State>``).
"""

from __future__ import annotations

import pytest

from kayak.web.build.shell import _build_nav, _build_placeholder_page

# The states with visible reaches in the live DB — what all_state_names() returns
# and what S3b-2 drives nav/landing pages off of (no hardcoded allowlist).
_STATES = ["California", "Idaho", "Montana", "Nevada", "Oregon", "Washington"]


@pytest.mark.parametrize("state", _STATES)
def test_landing_page_has_gauge_cross_links(state: str) -> None:
    """Every state landing page links to the filtered gauges view + gauge picker."""
    html = _build_placeholder_page("", _STATES, state)
    assert f'href="/gauges.html#st={state}"' in html, f"{state}.html missing gauges fragment link"
    assert f'href="/gauge_picker.php?state={state}"' in html, (
        f"{state}.html missing gauge picker link"
    )


@pytest.mark.parametrize("state", _STATES)
def test_landing_page_has_reach_cross_links_when_state_has_reaches(state: str) -> None:
    """States with reaches get the reaches + reach-picker anchors."""
    html = _build_placeholder_page("", _STATES, state)
    assert f'href="/index.html#st={state}"' in html, f"{state}.html missing reaches fragment link"
    assert f'href="/picker.php?state={state}"' in html, f"{state}.html missing reach picker link"


def test_landing_omits_reach_cross_links_for_non_reach_state() -> None:
    """A state not in the reach-states list has its body reach + reach-picker
    cross-link anchors suppressed; the gauge-side anchors still render. The build
    only emits pages for reach-states, so this exercises the defensive suppression
    in _build_placeholder_page directly."""
    html = _build_placeholder_page("", _STATES, "Wyoming")  # not in _STATES
    assert "→ Reaches in Wyoming" not in html
    assert "→ Reach picker — Wyoming" not in html
    # Gauge-side cross-links still present.
    assert "→ Live Wyoming gauges" in html
    assert "→ Gauge picker — Wyoming" in html
    assert "/gauges.html#st=Wyoming" in html
    assert "/gauge_picker.php?state=Wyoming" in html


def test_nav_bar_reach_picker_carries_active_state() -> None:
    """The nav-bar Reach Picker link picks up ?state=<active_state>."""
    html = _build_nav(_STATES, active_state="Oregon", picker_kind="reach")
    assert 'href="/picker.php?state=Oregon"' in html


def test_nav_bar_gauge_picker_carries_active_state() -> None:
    """Symmetric: the nav-bar Gauge Picker link carries the active state too."""
    html = _build_nav(_STATES, active_state="Montana", picker_kind="gauge")
    assert 'href="/gauge_picker.php?state=Montana"' in html


def test_nav_bar_picker_omits_state_when_no_active_state() -> None:
    """All-states pages (no active_state) get the bare picker URL."""
    html = _build_nav(_STATES, active_state="", picker_kind="reach")
    assert 'href="/picker.php"' in html
    assert "?state=" not in html.split('href="/picker.php"')[1].split("</a>")[0]


def test_nav_bar_shows_exactly_the_passed_states() -> None:
    """S3b-2: the header nav buttons are the passed `states` (all_state_names()),
    not a hardcoded allowlist — a state in the list gets a button, one not in it
    doesn't."""
    html = _build_nav(_STATES, active_state="Oregon")
    assert 'href="/Montana.html"' in html and ">MT</a>" in html  # in the list → button
    assert 'href="/Wyoming.html"' not in html  # not in the list → no button
