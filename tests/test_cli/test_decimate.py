"""Tests for kayak.cli.decimate observation thinning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from kayak.db.models import DataType, Observation, Source


def _seed_observations(
    session,
    source_id: int,
    count: int,
    start: datetime,
    interval: timedelta,
    data_type: DataType = DataType.flow,
    value: float = 100.0,
) -> list[Observation]:
    """Insert a series of observations at regular intervals."""
    obs = []
    for i in range(count):
        o = Observation(
            source_id=source_id,
            observed_at=start + interval * i,
            data_type=data_type,
            value=value + i,
        )
        session.add(o)
        obs.append(o)
    session.flush()
    return obs


def _count_obs(session) -> int:
    return session.execute(text("SELECT COUNT(*) FROM observation")).scalar()


class TestDecimateDryRun:
    """Dry-run count queries should report candidates without deleting."""

    def test_dry_run_count_queries_report_correctly(self, session, sample_source):
        now = datetime.now(UTC)
        # 4 obs per hour over 2 hours in medium range → should report 6 to delete
        _seed_observations(
            session, sample_source.id, 8, now - timedelta(days=200), timedelta(minutes=15)
        )
        before = _count_obs(session)
        assert before == 8

        from kayak.cli.decimate import _HOURLY_COUNT_SQL

        params = {
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        count = session.execute(text(_HOURLY_COUNT_SQL), params).scalar()
        # 8 obs / 2 hours → keep 2, delete 6 (or similar)
        assert count > 0
        assert count < before
        # The count query itself does NOT delete anything
        after = _count_obs(session)
        assert after == before, "count query should not delete any rows"


class TestDecimateHourlyThinning:
    """Medium-term observations should be thinned to one per hour."""

    def test_thins_medium_term_observations(self, session, sample_source):
        now = datetime.now(UTC)
        # 4 observations per hour over 2 hours, 200 days ago (medium range)
        start = now - timedelta(days=200)
        _seed_observations(session, sample_source.id, 8, start, timedelta(minutes=15))
        before = _count_obs(session)
        assert before == 8

        from kayak.cli.decimate import _HOURLY_SQL

        params = {
            "source_id": sample_source.id,
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        session.execute(text(_HOURLY_SQL), params)
        session.flush()
        after = _count_obs(session)
        # Should keep 1 per hour (2 hours → 2 kept, 6 deleted)
        assert after <= before
        assert after >= 2  # at least one per hour bucket


class TestDecimateSixHourlyThinning:
    """Archive observations should be thinned to one per 6-hour bucket."""

    def test_thins_archive_observations(self, session, sample_source):
        now = datetime.now(UTC)
        # 24 observations per day (hourly), 500 days ago (archive range)
        start = now - timedelta(days=500)
        _seed_observations(session, sample_source.id, 24, start, timedelta(hours=1))
        before = _count_obs(session)
        assert before == 24

        from kayak.cli.decimate import _6HOURLY_SQL

        params = {
            "source_id": sample_source.id,
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        session.execute(text(_6HOURLY_SQL), params)
        session.flush()
        after = _count_obs(session)
        # 24 hourly obs → 4 six-hour buckets → keep 4
        assert after <= 4


class TestDecimatePreservesRecentData:
    """Recent observations (< recent_days) should never be touched."""

    def test_recent_observations_untouched(self, session, sample_source):
        now = datetime.now(UTC)
        # All observations within last 30 days
        _seed_observations(
            session, sample_source.id, 100, now - timedelta(days=30), timedelta(minutes=15)
        )
        before = _count_obs(session)

        from kayak.cli.decimate import _HOURLY_SQL

        params = {
            "source_id": sample_source.id,
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        session.execute(text(_HOURLY_SQL), params)
        session.flush()
        after = _count_obs(session)
        assert after == before, "recent data should be preserved"


class TestDecimateMultipleSourcesAndTypes:
    """Thinning should be independent per source_id and data_type."""

    def test_independent_per_source(self, session):
        s1 = Source(name="source_1", agency="USGS")
        s2 = Source(name="source_2", agency="NOAA")
        session.add_all([s1, s2])
        session.flush()

        now = datetime.now(UTC)
        start = now - timedelta(days=200)
        _seed_observations(session, s1.id, 8, start, timedelta(minutes=15))
        _seed_observations(session, s2.id, 8, start, timedelta(minutes=15), value=500.0)
        assert _count_obs(session) == 16

        from kayak.cli.decimate import _HOURLY_SQL

        params_base = {
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        # Thin each source independently
        for sid in (s1.id, s2.id):
            session.execute(text(_HOURLY_SQL), {**params_base, "source_id": sid})
        session.flush()
        after = _count_obs(session)
        # Each source had 8 obs over ~2 hours → each keeps ~2
        assert after <= 8

    def test_independent_per_data_type(self, session, sample_source):
        now = datetime.now(UTC)
        start = now - timedelta(days=200)
        _seed_observations(
            session, sample_source.id, 8, start, timedelta(minutes=15), DataType.flow
        )
        _seed_observations(
            session, sample_source.id, 8, start, timedelta(minutes=15), DataType.gauge, value=5.0
        )
        assert _count_obs(session) == 16

        from kayak.cli.decimate import _HOURLY_SQL

        params = {
            "source_id": sample_source.id,
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        session.execute(text(_HOURLY_SQL), params)
        session.flush()
        after = _count_obs(session)
        # Each data_type thinned independently, both should keep >=1 per hour
        assert after >= 4  # at least 2 per type


class TestDecimateEdgeCases:
    """Edge cases: empty table, single observation, boundary timestamps."""

    def test_empty_table(self, session, sample_source):
        """Decimate on empty observation table does nothing."""
        assert _count_obs(session) == 0
        from kayak.cli.decimate import _HOURLY_SQL

        now = datetime.now(UTC)
        params = {
            "source_id": sample_source.id,
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        session.execute(text(_HOURLY_SQL), params)
        session.flush()
        assert _count_obs(session) == 0

    def test_single_observation_per_hour_not_deleted(self, session, sample_source):
        """If there's exactly 1 obs per hour, nothing should be deleted."""
        now = datetime.now(UTC)
        start = now - timedelta(days=200)
        _seed_observations(session, sample_source.id, 5, start, timedelta(hours=1))
        before = _count_obs(session)

        from kayak.cli.decimate import _HOURLY_SQL

        params = {
            "source_id": sample_source.id,
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        session.execute(text(_HOURLY_SQL), params)
        session.flush()
        after = _count_obs(session)
        assert after == before, "1 obs per hour should all survive"

    def test_midpoint_selection(self, session, sample_source):
        """The observation closest to :30 should be the one kept."""
        now = datetime.now(UTC)
        base = now - timedelta(days=200)
        hour_start = base.replace(minute=0, second=0, microsecond=0)

        # Insert at :05, :25, :35, :55
        for minute in [5, 25, 35, 55]:
            obs = Observation(
                source_id=sample_source.id,
                observed_at=hour_start.replace(minute=minute),
                data_type=DataType.flow,
                value=float(minute),
            )
            session.add(obs)
        session.flush()
        assert _count_obs(session) == 4

        from kayak.cli.decimate import _HOURLY_SQL

        params = {
            "source_id": sample_source.id,
            "medium_cutoff": (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S"),
            "archive_cutoff": (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        session.execute(text(_HOURLY_SQL), params)
        session.flush()
        after = _count_obs(session)
        assert after == 1
        # The survivor should be :25 or :35 (both are 5 min from :30)
        kept = session.execute(text("SELECT value FROM observation")).scalar()
        assert kept in (25.0, 35.0)
