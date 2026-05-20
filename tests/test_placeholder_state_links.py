"""Pin the cross-link anchors emitted by ``_build_placeholder_page``.

These guard the entry-point UX from state landing pages
(``Oregon.html`` / ``Montana.html`` / etc.) into the filtered all-states
views (``gauges.html#st=<State>`` etc.) and the pre-filtered pickers
(``picker.php?state=<State>``, ``gauge_picker.php?state=<State>``).
"""

from __future__ import annotations

import pytest

from kayak.web.build._shared import _NAV_STATES
from kayak.web.build.shell import _build_nav, _build_placeholder_page

# Reach-presence states (subset of _NAV_STATES that have visible reaches in the live DB).
_REACH_STATES = ["California", "Idaho", "Nevada", "Oregon", "Washington"]


@pytest.mark.parametrize("state", sorted(_NAV_STATES))
def test_landing_page_has_gauge_cross_links(state: str) -> None:
    """Every state landing page links to the filtered gauges view + gauge picker."""
    html = _build_placeholder_page("", _REACH_STATES, state)
    assert f'href="/gauges.html#st={state}"' in html, f"{state}.html missing gauges fragment link"
    assert f'href="/gauge_picker.php?state={state}"' in html, (
        f"{state}.html missing gauge picker link"
    )


@pytest.mark.parametrize("state", _REACH_STATES)
def test_landing_page_has_reach_cross_links_when_state_has_reaches(state: str) -> None:
    """States with reaches get the reaches + reach-picker anchors."""
    html = _build_placeholder_page("", _REACH_STATES, state)
    assert f'href="/index.html#st={state}"' in html, f"{state}.html missing reaches fragment link"
    assert f'href="/picker.php?state={state}"' in html, f"{state}.html missing reach picker link"


def test_montana_landing_omits_reach_cross_links() -> None:
    """Montana has gauges but no reaches in scope — the body cross-link anchors
    for reaches + reach-picker are suppressed (the nav-bar still carries
    a Reach Picker link, which is global navigation chrome)."""
    html = _build_placeholder_page("", _REACH_STATES, "Montana")
    # No "Reaches in Montana" body cross-link anchor.
    assert "→ Reaches in Montana" not in html
    assert "→ Reach picker — Montana" not in html
    # Gauge-side cross-links still present.
    assert "→ Live Montana gauges" in html
    assert "→ Gauge picker — Montana" in html
    # And the underlying URLs for the gauge-side anchors.
    assert "/gauges.html#st=Montana" in html
    assert "/gauge_picker.php?state=Montana" in html


def test_nav_bar_reach_picker_carries_active_state() -> None:
    """The nav-bar Reach Picker link picks up ?state=<active_state>."""
    html = _build_nav(_REACH_STATES, active_state="Oregon", picker_kind="reach")
    assert 'href="/picker.php?state=Oregon"' in html


def test_nav_bar_gauge_picker_carries_active_state() -> None:
    """Symmetric: the nav-bar Gauge Picker link carries the active state too."""
    html = _build_nav(_REACH_STATES, active_state="Montana", picker_kind="gauge")
    assert 'href="/gauge_picker.php?state=Montana"' in html


def test_nav_bar_picker_omits_state_when_no_active_state() -> None:
    """All-states pages (no active_state) get the bare picker URL."""
    html = _build_nav(_REACH_STATES, active_state="", picker_kind="reach")
    assert 'href="/picker.php"' in html
    assert "?state=" not in html.split('href="/picker.php"')[1].split("</a>")[0]


def test_nav_bar_includes_montana_button() -> None:
    """Header nav shows every _NAV_STATES entry, not just reach-states."""
    html = _build_nav(_REACH_STATES, active_state="Oregon")
    assert 'href="/Montana.html"' in html
    assert ">MT</a>" in html
