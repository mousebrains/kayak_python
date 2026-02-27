"""Tests for kayak.config default values and types."""

from kayak import config


class TestConfigDefaults:
    def test_database_url_is_string(self):
        assert isinstance(config.DATABASE_URL, str)

    def test_debug_is_bool(self):
        assert isinstance(config.DEBUG, bool)

    def test_fetch_timeout_is_int(self):
        assert isinstance(config.FETCH_TIMEOUT, int)
        assert config.FETCH_TIMEOUT > 0

    def test_fetch_user_agent_is_string(self):
        assert isinstance(config.FETCH_USER_AGENT, str)
        assert len(config.FETCH_USER_AGENT) > 0
