"""Tests for kayak.db.engine singleton management and SQLite pragmas."""

from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.orm import Session

from kayak.db.engine import get_engine, get_session, get_session_factory, reset


class TestGetEngine:
    def teardown_method(self) -> None:
        reset()

    def test_returns_singleton(self):
        e1 = get_engine("sqlite:///:memory:")
        e2 = get_engine()
        assert e1 is e2

    def test_url_creates_new(self):
        e1 = get_engine("sqlite:///:memory:")
        e2 = get_engine("sqlite:///:memory:")
        assert e1 is not e2

    def test_reset_clears(self):
        get_engine("sqlite:///:memory:")
        reset()
        # After reset, a new call creates a fresh engine
        import kayak.db.engine as mod

        assert mod._engine is None
        assert mod._session_factory is None

    def test_url_override_disposes_prior_engine(self):
        """Passing url=... should dispose the prior engine before replacing it.

        Without this, every rebind orphans the previous connection pool and
        the DB file stays open until GC eventually clears it — which matters
        for tests that rapidly swap SQLite files.
        """
        e1 = get_engine("sqlite:///:memory:")
        with patch.object(e1, "dispose", wraps=e1.dispose) as spy:
            e2 = get_engine("sqlite:///:memory:")
            assert e1 is not e2
            spy.assert_called_once()

    def test_url_override_invalidates_session_factory(self):
        """Replacing the engine via get_engine(url=...) must drop the cached
        session factory so a subsequent get_session_factory() rebuilds it
        against the new engine.

        Regression: previously the factory was only rebuilt when
        get_session_factory() itself was called with a url. If a caller went
        through get_engine() directly, the old factory stayed bound to the
        now-disposed engine — sessions created from it would fail or, worse,
        write to a stale connection.
        """
        # Prime both caches
        get_engine("sqlite:///:memory:")
        f1 = get_session_factory()

        # Swap the engine via get_engine() directly
        e2 = get_engine("sqlite:///:memory:")

        # A subsequent get_session_factory() with no url must NOT return f1.
        # It should rebuild against e2.
        f2 = get_session_factory()
        assert f2 is not f1
        assert f2.kw["bind"] is e2


class TestSQLitePragmas:
    def teardown_method(self) -> None:
        reset()

    def test_foreign_keys_enabled(self):
        engine = get_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
            assert fk == 1

    def test_wal_mode(self):
        engine = get_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            # In-memory databases may report 'memory' instead of 'wal'
            assert mode in ("wal", "memory")

    def test_mmap_size_configured(self, tmp_path):
        # PRAGMA mmap_size only takes effect on file-backed databases —
        # in-memory DBs report 0 regardless of the requested size.
        db_path = tmp_path / "mmap.db"
        engine = get_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            mmap = conn.execute(text("PRAGMA mmap_size")).scalar()
            assert mmap == 134217728

    def test_cache_size_configured(self):
        engine = get_engine("sqlite:///:memory:")
        with engine.connect() as conn:
            cache = conn.execute(text("PRAGMA cache_size")).scalar()
            # Negative cache_size is in KB, positive in pages.
            assert cache == -16000


class TestGetSession:
    def teardown_method(self) -> None:
        reset()

    def test_returns_session(self):
        session = get_session("sqlite:///:memory:")
        assert isinstance(session, Session)
        session.close()

    def test_factory_returns_sessionmaker(self):
        factory = get_session_factory("sqlite:///:memory:")
        session = factory()
        assert isinstance(session, Session)
        session.close()
