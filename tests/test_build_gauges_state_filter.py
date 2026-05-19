"""State-scoped gauges page (`gauges.<state>.html`) — filter behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kayak.db.models import DataType, Gauge, LatestGaugeObservation
from kayak.web.build.gauges import _write_gauges_page
from kayak.web.build.shell import _build_placeholder_page

# (state_abbrev, full_name, slug) — every state we currently emit a
# gauges.<slug>.html page for. New states get a row here and in deploy.py.
_STATE_CASES = [
    ("MT", "Montana", "montana"),
    ("OR", "Oregon", "oregon"),
    ("WA", "Washington", "washington"),
    ("ID", "Idaho", "idaho"),
]


def _seed_gauge(session, *, name: str, state: str, huc: str) -> Gauge:
    g = Gauge(
        name=name,
        usgs_id=name,
        state=state,
        huc=huc,
        river=name.replace("_", " "),
        location=name,
        display_name=name,
        sort_name=f"{name.lower()}|9|005000|000100",
        latitude=46.0,
        longitude=-114.0,
    )
    session.add(g)
    session.flush()
    return g


def _seed_obs(session, gauge_id: int, value: float = 500.0) -> None:
    session.add(
        LatestGaugeObservation(
            gauge_id=gauge_id,
            data_type=DataType.flow,
            observed_at=datetime.now(UTC),
            value=value,
        )
    )
    session.flush()


def _all_latest(session) -> dict:
    """Mirror the build's all_latest dict shape — keyed by (gauge_id, data_type)."""
    return {
        (row.gauge_id, row.data_type): row for row in session.query(LatestGaugeObservation).all()
    }


@pytest.mark.parametrize(("abbrev", "full_name", "slug"), _STATE_CASES)
def test_state_scoped_page_filters_rows(
    session, tmp_path: Path, abbrev: str, full_name: str, slug: str
) -> None:
    """state=<abbrev> emits gauges.<slug>.html with only that state's rows."""
    (tmp_path / "static").mkdir()
    # Seed the target gauge plus a noise gauge in a different state. When the
    # target IS Oregon, use Washington as the noise; otherwise Oregon.
    noise_abbrev = "WA" if abbrev == "OR" else "OR"
    target = _seed_gauge(session, name="11111111", state=abbrev, huc="17010205")
    noise = _seed_gauge(session, name="22222222", state=noise_abbrev, huc="17090011")
    _seed_obs(session, target.id)
    _seed_obs(session, noise.id)

    written = _write_gauges_page(
        session,
        _all_latest(session),
        states=[full_name],
        css_link="",
        output_dir=tmp_path,
        state=abbrev,
    )

    assert written is True
    page = (tmp_path / f"gauges.{slug}.html").read_text()
    assert "11111111" in page
    assert "22222222" not in page
    # Title and canonical reflect the state scope.
    assert full_name in page
    assert f"/gauges.{slug}.html" in page
    # Filter bar omits the redundant state row on a single-state page
    # (existing _build_filter_bar is_all_page=False behavior).
    assert 'data-group="state"' not in page


@pytest.mark.parametrize(("abbrev", "full_name", "slug"), _STATE_CASES)
def test_state_scoped_page_returns_false_when_empty(
    session, tmp_path: Path, abbrev: str, full_name: str, slug: str
) -> None:
    """No matching gauges → returns False, no file written."""
    (tmp_path / "static").mkdir()
    # Seed an irrelevant gauge in a state that won't match.
    noise_abbrev = "WA" if abbrev == "OR" else "OR"
    noise = _seed_gauge(session, name="22222222", state=noise_abbrev, huc="17090011")
    _seed_obs(session, noise.id)

    written = _write_gauges_page(
        session,
        _all_latest(session),
        states=[full_name],
        css_link="",
        output_dir=tmp_path,
        state=abbrev,
    )

    assert written is False
    assert not (tmp_path / f"gauges.{slug}.html").exists()


def test_all_states_page_unchanged_when_state_unset(session, tmp_path: Path) -> None:
    """Default call (state=None) still writes gauges.html, returns True."""
    (tmp_path / "static").mkdir()
    orr = _seed_gauge(session, name="14306500", state="OR", huc="17090011")
    _seed_obs(session, orr.id)

    written = _write_gauges_page(
        session,
        _all_latest(session),
        states=["Oregon"],
        css_link="",
        output_dir=tmp_path,
    )

    assert written is True
    assert (tmp_path / "gauges.html").exists()
    assert not (tmp_path / "gauges.montana.html").exists()


# ---------------------------------------------------------------------------
# Cross-link from per-state placeholder page → gauges.<state>.html
# ---------------------------------------------------------------------------


def test_placeholder_page_includes_live_gauges_link_when_state_has_page() -> None:
    """Oregon.html (etc.) gets a live-data anchor when the state's gauges page exists."""
    html = _build_placeholder_page(
        css_link="",
        states=["Oregon"],
        state="Oregon",
        gauge_state_pages={"Oregon"},
    )
    assert 'href="/gauges.oregon.html"' in html
    assert "Live Oregon gauge readings" in html


def test_placeholder_page_omits_live_gauges_link_when_state_absent() -> None:
    """When the state isn't in the set (no gauges page emitted), no anchor."""
    html = _build_placeholder_page(
        css_link="",
        states=["Oregon"],
        state="Oregon",
        gauge_state_pages=set(),
    )
    assert "/gauges.oregon.html" not in html
    assert "Live Oregon gauge readings" not in html


def test_placeholder_page_default_no_gauge_state_pages_kwarg() -> None:
    """Back-compat: omitting the kwarg behaves like an empty set."""
    html = _build_placeholder_page(css_link="", states=["Oregon"], state="Oregon")
    assert "/gauges.oregon.html" not in html
