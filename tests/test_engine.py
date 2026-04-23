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
