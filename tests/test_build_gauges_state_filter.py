"""State-scoped gauges page (`gauges.<state>.html`) — filter behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from kayak.db.models import DataType, Gauge, LatestGaugeObservation
from kayak.web.build.gauges import _write_gauges_page


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
        (row.gauge_id, row.data_type): row
        for row in session.query(LatestGaugeObservation).all()
    }


def test_state_scoped_page_filters_rows(session, tmp_path: Path) -> None:
    """state="MT" emits gauges.montana.html with only MT rows."""
    (tmp_path / "static").mkdir()
    mt = _seed_gauge(session, name="12345678", state="MT", huc="17010205")
    orr = _seed_gauge(session, name="14306500", state="OR", huc="17090011")
    _seed_obs(session, mt.id)
    _seed_obs(session, orr.id)

    written = _write_gauges_page(
        session, _all_latest(session), states=["Oregon"], css_link="",
        output_dir=tmp_path, state="MT",
    )

    assert written is True
    page = (tmp_path / "gauges.montana.html").read_text()
    assert "12345678" in page
    assert "14306500" not in page
    # Title and canonical reflect the state scope.
    assert "Montana" in page
    assert "/gauges.montana.html" in page
    # Filter bar omits the redundant state row on a single-state page
    # (existing _build_filter_bar is_all_page=False behavior).
    assert 'data-group="state"' not in page


def test_state_scoped_page_returns_false_when_empty(
    session, tmp_path: Path
) -> None:
    """No matching gauges -> returns False, no file written."""
    (tmp_path / "static").mkdir()
    orr = _seed_gauge(session, name="14306500", state="OR", huc="17090011")
    _seed_obs(session, orr.id)

    written = _write_gauges_page(
        session, _all_latest(session), states=["Oregon"], css_link="",
        output_dir=tmp_path, state="MT",
    )

    assert written is False
    assert not (tmp_path / "gauges.montana.html").exists()


def test_all_states_page_unchanged_when_state_unset(
    session, tmp_path: Path
) -> None:
    """Default call (state=None) still writes gauges.html, returns True."""
    (tmp_path / "static").mkdir()
    orr = _seed_gauge(session, name="14306500", state="OR", huc="17090011")
    _seed_obs(session, orr.id)

    written = _write_gauges_page(
        session, _all_latest(session), states=["Oregon"], css_link="",
        output_dir=tmp_path,
    )

    assert written is True
    assert (tmp_path / "gauges.html").exists()
    assert not (tmp_path / "gauges.montana.html").exists()
