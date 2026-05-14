"""Tests for kayak.db.sources helpers."""

from datetime import UTC, datetime, timedelta

from kayak.db.models import (
    DataType,
    FetchUrl,
    Gauge,
    GaugeSource,
    LatestObservation,
    Source,
)
from kayak.db.sources import find_orphan_sources


def _mk_fetch_url(session, url: str, *, is_active: bool = True) -> FetchUrl:
    fu = FetchUrl(url=url, parser="nwps", is_active=is_active)
    session.add(fu)
    session.flush()
    return fu


def _mk_source(session, name: str, *, fetch_url_id: int | None = None) -> Source:
    src = Source(name=name, fetch_url_id=fetch_url_id)
    session.add(src)
    session.flush()
    return src


def _mk_gauge(session, name: str = "test-gauge") -> Gauge:
    g = Gauge(name=name)
    session.add(g)
    session.flush()
    return g


def _mk_latest_obs(
    session, source_id: int, observed_at: datetime, data_type: DataType = DataType.flow
) -> None:
    session.add(
        LatestObservation(
            source_id=source_id,
            data_type=data_type,
            observed_at=observed_at,
            value=100.0,
        )
    )
    session.flush()


class TestFindOrphanSources:
    """Active fetch-backed sources missing a gauge_source link."""

    def test_returns_empty_when_no_sources(self, session):
        assert find_orphan_sources(session) == []

    def test_flags_active_fetch_source_with_no_gauge_link(self, session):
        fu = _mk_fetch_url(session, "https://example.com/orphan")
        src = _mk_source(session, "orphan-station", fetch_url_id=fu.id)
        _mk_latest_obs(session, src.id, datetime.now(UTC))

        rows = find_orphan_sources(session)
        assert len(rows) == 1
        assert rows[0].source_id == src.id
        assert rows[0].name == "orphan-station"
        assert rows[0].url == "https://example.com/orphan"
        assert rows[0].is_active is True

    def test_excludes_linked_source(self, session):
        fu = _mk_fetch_url(session, "https://example.com/linked")
        src = _mk_source(session, "linked-station", fetch_url_id=fu.id)
        gauge = _mk_gauge(session)
        session.add(GaugeSource(gauge_id=gauge.id, source_id=src.id))
        session.flush()

        assert find_orphan_sources(session) == []

    def test_excludes_calc_only_source(self, session):
        # No fetch_url_id at all (calc source); must not be flagged.
        _mk_source(session, "calc-source", fetch_url_id=None)
        assert find_orphan_sources(session) == []

    def test_excludes_inactive_url_with_old_observations(self, session):
        # Inactive URL + observation older than 7 days → drop off.
        fu = _mk_fetch_url(session, "https://example.com/retired", is_active=False)
        src = _mk_source(session, "retired-station", fetch_url_id=fu.id)
        _mk_latest_obs(session, src.id, datetime.now(UTC) - timedelta(days=14))

        assert find_orphan_sources(session) == []

    def test_includes_inactive_url_with_recent_observations(self, session):
        # Inactive URL but obs within 7 days → still flag (the cleanup race
        # case: URL just got deactivated but no one has rewired/removed the
        # source row yet).
        fu = _mk_fetch_url(session, "https://example.com/race", is_active=False)
        src = _mk_source(session, "race-station", fetch_url_id=fu.id)
        _mk_latest_obs(session, src.id, datetime.now(UTC) - timedelta(days=1))

        rows = find_orphan_sources(session)
        assert len(rows) == 1
        assert rows[0].source_id == src.id
        assert rows[0].is_active is False

    def test_picks_max_observed_at_across_data_types(self, session):
        # A source emitting multiple data_types must report the freshest
        # observed_at, not an arbitrary one (the un-aggregated SELECT
        # would let SQLite pick any per its lax-aggregation rules).
        fu = _mk_fetch_url(session, "https://example.com/multi")
        src = _mk_source(session, "multi-station", fetch_url_id=fu.id)
        now = datetime.now(UTC)
        _mk_latest_obs(session, src.id, now - timedelta(hours=2), data_type=DataType.flow)
        _mk_latest_obs(session, src.id, now, data_type=DataType.gauge)

        rows = find_orphan_sources(session)
        assert len(rows) == 1
        assert rows[0].latest_obs is not None
        # The freshest is `now` (gauge), not 2h ago (flow).
        assert abs((rows[0].latest_obs - now.replace(tzinfo=None)).total_seconds()) < 1
