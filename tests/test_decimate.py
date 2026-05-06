"""Tests for the decimate (observation thinning) command."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from kayak.db.models import DataType, FetchUrl, Source
from kayak.db.observations import store_observations


def _make_source(session, name="src1"):
    """Helper to create a Source with FetchUrl."""
    fu = FetchUrl(url=f"https://example.com/{name}", parser="nwps", is_active=True)
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
        row[0]
        for row in session.execute(
            text("SELECT DISTINCT source_id FROM observation WHERE observed_at < :medium_cutoff"),
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
            rows.append(
                {
                    "source_id": src.id,
                    "data_type": DataType.flow,
                    "observed_at": base + timedelta(hours=hour, minutes=minute),
                    "value": 100.0 + hour,
                }
            )
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
        rows.append(
            {
                "source_id": src.id,
                "data_type": DataType.flow,
                "observed_at": base + timedelta(hours=hour),
                "value": 100.0 + hour,
            }
        )
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
        rows.append(
            {
                "source_id": src.id,
                "data_type": DataType.flow,
                "observed_at": now - timedelta(hours=i),
                "value": 100.0 + i,
            }
        )
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
            rows.append(
                {
                    "source_id": src.id,
                    "data_type": DataType.flow,
                    "observed_at": base + timedelta(hours=hour, minutes=minute),
                    "value": 100.0,
                }
            )
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
        session,
        medium_cutoff,
        archive_cutoff,
    )
    assert hourly_count == 0
    assert sixhour_count == 0


# ---------------------------------------------------------------------------
# End-to-end decimate() wrapper tests — exercise the CLI entry point against
# a real sqlite file so the delete loop, cache refresh, and PRAGMA optimize
# paths all get coverage (not just the raw SQL).
# ---------------------------------------------------------------------------


def _bootstrap_engine_for_cli(tmp_path):
    """Create a dedicated sqlite file DB and bind the module engine to it.

    Returns the (engine, db_path) pair. Caller is responsible for calling
    kayak.db.engine.reset() afterwards.
    """
    from kayak.db.engine import get_engine
    from kayak.db.models import Base

    db_path = tmp_path / "decimate_e2e.db"
    engine = get_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine, db_path


def test_decimate_cli_end_to_end(tmp_path):
    """Exercise decimate() against a real sqlite file.

    Seeds a source + FetchUrl + ~30 observations spanning recent /
    medium-term / archive ranges, runs decimate(), and asserts the
    medium-term rows get thinned to ~1 per hour while recent rows survive.
    """
    from argparse import Namespace

    from kayak.cli.decimate import decimate
    from kayak.db.engine import get_session, reset
    from kayak.db.models import DataType, FetchUrl, GaugeSource, Observation, Source
    from kayak.db.models import Gauge as GaugeModel

    _engine, _db_path = _bootstrap_engine_for_cli(tmp_path)
    try:
        s = get_session()
        try:
            fu = FetchUrl(url="https://example.com/e2e", parser="nwps", is_active=True)
            s.add(fu)
            s.flush()
            src = Source(name="e2e_src", fetch_url_id=fu.id)
            s.add(src)
            s.flush()
            # Link a gauge so update_all_latest_gauges has work to do.
            g = GaugeModel(name="e2e_gauge")
            s.add(g)
            s.flush()
            s.add(GaugeSource(gauge_id=g.id, source_id=src.id))
            s.flush()

            now = datetime.now(UTC)
            # 4 recent rows — should be untouched
            for i in range(4):
                s.add(
                    Observation(
                        source_id=src.id,
                        data_type=DataType.flow,
                        observed_at=now - timedelta(days=5, minutes=15 * i),
                        value=100.0 + i,
                    )
                )
            # 24 medium-term rows (200 days ago, every 15 min over 6 hours) —
            # should collapse to ~6 (1 per hour).
            base_medium = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=200)
            for hour in range(6):
                for minute in (0, 15, 30, 45):
                    s.add(
                        Observation(
                            source_id=src.id,
                            data_type=DataType.flow,
                            observed_at=base_medium + timedelta(hours=hour, minutes=minute),
                            value=500.0 + hour,
                        )
                    )
            s.commit()

            total_before = s.query(Observation).count()
            assert total_before == 28

            args = Namespace(recent_days=90, archive_days=365, dry_run=False, vacuum=False)
            decimate(args)

            # Re-read via a fresh session so any caching from the CLI doesn't
            # mask the delete.
            s.close()
            s = get_session()
            total_after = s.query(Observation).count()
            # 4 recent + ~6 medium (1 per hour bucket) = ~10
            assert total_after < total_before
            assert total_after <= 12
            assert total_after >= 8
        finally:
            s.close()
    finally:
        reset()


def test_decimate_cli_dry_run(tmp_path):
    """--dry-run reports counts but deletes nothing."""
    from argparse import Namespace

    from kayak.cli.decimate import decimate
    from kayak.db.engine import get_session, reset
    from kayak.db.models import DataType, FetchUrl, Observation, Source

    _engine, _db_path = _bootstrap_engine_for_cli(tmp_path)
    try:
        s = get_session()
        try:
            fu = FetchUrl(url="https://example.com/dryrun", parser="nwps", is_active=True)
            s.add(fu)
            s.flush()
            src = Source(name="dryrun_src", fetch_url_id=fu.id)
            s.add(src)
            s.flush()

            now = datetime.now(UTC)
            base = now.replace(minute=0, second=0, microsecond=0) - timedelta(days=200)
            for hour in range(4):
                for minute in (0, 15, 30, 45):
                    s.add(
                        Observation(
                            source_id=src.id,
                            data_type=DataType.flow,
                            observed_at=base + timedelta(hours=hour, minutes=minute),
                            value=200.0,
                        )
                    )
            s.commit()
            before = s.query(Observation).count()
            assert before == 16

            args = Namespace(recent_days=90, archive_days=365, dry_run=True, vacuum=False)
            decimate(args)

            s.close()
            s = get_session()
            after = s.query(Observation).count()
            assert after == before, "dry-run must not delete anything"
        finally:
            s.close()
    finally:
        reset()


def test_decimate_cli_nothing_to_do(tmp_path):
    """Running decimate with only recent data triggers the 'Nothing to decimate' path."""
    from argparse import Namespace

    from kayak.cli.decimate import decimate
    from kayak.db.engine import get_session, reset
    from kayak.db.models import DataType, FetchUrl, Observation, Source

    _engine, _db_path = _bootstrap_engine_for_cli(tmp_path)
    try:
        s = get_session()
        try:
            fu = FetchUrl(url="https://example.com/quiet", parser="nwps", is_active=True)
            s.add(fu)
            s.flush()
            src = Source(name="quiet_src", fetch_url_id=fu.id)
            s.add(src)
            s.flush()

            now = datetime.now(UTC)
            for i in range(10):
                s.add(
                    Observation(
                        source_id=src.id,
                        data_type=DataType.flow,
                        observed_at=now - timedelta(hours=i),
                        value=50.0,
                    )
                )
            s.commit()

            args = Namespace(recent_days=90, archive_days=365, dry_run=False, vacuum=False)
            decimate(args)

            s.close()
            s = get_session()
            assert s.query(Observation).count() == 10
        finally:
            s.close()
    finally:
        reset()
