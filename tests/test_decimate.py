"""Tests for the decimate (observation thinning) command."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from kayak.db.data_db import store_observations
from kayak.db.models import DataType, FetchUrl, Source


def _make_source(session, name="src1"):
    """Helper to create a Source with FetchUrl."""
    fu = FetchUrl(url=f"https://example.com/{name}", parser="usgs", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


def _count_obs(session):
    """Count total observations."""
    return session.execute(text("SELECT COUNT(*) FROM observation")).scalar()


def _run_decimate_sql(session, medium_cutoff, archive_cutoff, dry_run=False):
    """Run the decimate SQL directly (avoids CLI/engine plumbing in tests)."""
    from kayak.cli.decimate import (
        _6HOURLY_COUNT_SQL,
        _6HOURLY_SQL,
        _HOURLY_COUNT_SQL,
        _HOURLY_SQL,
    )

    params = {
        "medium_cutoff": medium_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        "archive_cutoff": archive_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
    }

    hourly_count = session.execute(text(_HOURLY_COUNT_SQL), params).scalar()
    sixhour_count = session.execute(
        text(_6HOURLY_COUNT_SQL),
        {"archive_cutoff": params["archive_cutoff"]},
    ).scalar()

    if dry_run:
        return hourly_count, sixhour_count

    # Delete per source_id, matching the batched production code
    source_ids = [
        row[0] for row in session.execute(
            text(
                "SELECT DISTINCT source_id FROM observation "
                "WHERE observed_at < :medium_cutoff"
            ),
            {"medium_cutoff": params["medium_cutoff"]},
        ).fetchall()
    ]
    for source_id in source_ids:
        src_params = {**params, "source_id": source_id}
        session.execute(text(_HOURLY_SQL), src_params)
        session.execute(
            text(_6HOURLY_SQL),
            {"archive_cutoff": params["archive_cutoff"], "source_id": source_id},
        )
    session.flush()
    return hourly_count, sixhour_count


def test_thin_hourly(session):
    """Observations at 15-min intervals are thinned to ~1 per hour."""
    src = _make_source(session)
    now = datetime.now(UTC)
    # Place observations 120 days ago, aligned to the start of an hour
    base = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=120)

    rows = []
    for hour in range(6):
        for minute in [0, 15, 30, 45]:
            rows.append({
                "source_id": src.id,
                "data_type": DataType.flow,
                "observed_at": base + timedelta(hours=hour, minutes=minute),
                "value": 100.0 + hour,
            })
    store_observations(session, rows)
    session.flush()

    assert _count_obs(session) == 24  # 6 hours * 4 per hour

    medium_cutoff = now - timedelta(days=90)
    archive_cutoff = now - timedelta(days=365)
    _run_decimate_sql(session, medium_cutoff, archive_cutoff)

    remaining = _count_obs(session)
    # Should keep 1 per hour = 6
    assert remaining == 6


def test_thin_6h(session):
    """Observations thinned to 1 per 6-hour bucket for archive range."""
    src = _make_source(session)
    now = datetime.now(UTC)
    # Place observations 400 days ago, aligned to midnight
    base = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=400)

    rows = []
    for hour in range(24):
        rows.append({
            "source_id": src.id,
            "data_type": DataType.flow,
            "observed_at": base + timedelta(hours=hour),
            "value": 100.0 + hour,
        })
    store_observations(session, rows)
    session.flush()

    assert _count_obs(session) == 24

    medium_cutoff = now - timedelta(days=90)
    archive_cutoff = now - timedelta(days=365)
    _run_decimate_sql(session, medium_cutoff, archive_cutoff)

    remaining = _count_obs(session)
    # 24 hours / 6-hour buckets = 4 observations
    assert remaining == 4


def test_recent_preserved(session):
    """Observations within the recent window are untouched."""
    src = _make_source(session)
    now = datetime.now(UTC)

    rows = []
    for i in range(20):
        rows.append({
            "source_id": src.id,
            "data_type": DataType.flow,
            "observed_at": now - timedelta(hours=i),
            "value": 100.0 + i,
        })
    store_observations(session, rows)
    session.flush()

    assert _count_obs(session) == 20

    medium_cutoff = now - timedelta(days=90)
    archive_cutoff = now - timedelta(days=365)
    _run_decimate_sql(session, medium_cutoff, archive_cutoff)

    assert _count_obs(session) == 20  # No change


def test_dry_run(session):
    """Dry run reports counts but does not delete."""
    src = _make_source(session)
    now = datetime.now(UTC)
    base = now - timedelta(days=120)

    rows = []
    for hour in range(6):
        for minute in [0, 15, 30, 45]:
            rows.append({
                "source_id": src.id,
                "data_type": DataType.flow,
                "observed_at": base + timedelta(hours=hour, minutes=minute),
                "value": 100.0,
            })
    store_observations(session, rows)
    session.flush()

    medium_cutoff = now - timedelta(days=90)
    archive_cutoff = now - timedelta(days=365)
    hourly_count, _ = _run_decimate_sql(session, medium_cutoff, archive_cutoff, dry_run=True)

    # Dry run should report deletions but not change data
    assert hourly_count > 0
    assert _count_obs(session) == 24


def test_empty_db(session):
    """Decimate on empty database doesn't crash."""
    now = datetime.now(UTC)
    medium_cutoff = now - timedelta(days=90)
    archive_cutoff = now - timedelta(days=365)

    hourly_count, sixhour_count = _run_decimate_sql(
        session, medium_cutoff, archive_cutoff,
    )
    assert hourly_count == 0
    assert sixhour_count == 0
