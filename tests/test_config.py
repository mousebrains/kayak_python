"""Tests for kayak.config defaults and the KayakConfig typed model."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from kayak import config
from kayak.config import KayakConfig


class TestConfigDefaults:
    """Module-level constants — frozen at import time for source-compat."""

    def test_database_url_is_string(self) -> None:
        assert isinstance(config.DATABASE_URL, str)

    def test_fetch_timeout_is_int(self) -> None:
        assert isinstance(config.FETCH_TIMEOUT, int)
        assert config.FETCH_TIMEOUT > 0

    def test_fetch_user_agent_is_string(self) -> None:
        assert isinstance(config.FETCH_USER_AGENT, str)
        assert len(config.FETCH_USER_AGENT) > 0

    def test_output_dir_is_string(self) -> None:
        assert isinstance(config.OUTPUT_DIR, str)


class TestKayakConfigEnvReads:
    """KayakConfig() picks up env vars at instantiation."""

    def test_env_override_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/override.db")
        cfg = KayakConfig()
        assert cfg.database_url == "sqlite:///tmp/override.db"

    def test_env_override_fetch_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FETCH_TIMEOUT", "120")
        cfg = KayakConfig()
        assert cfg.fetch_timeout == 120

    def test_env_override_editor_feature_bool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EDITOR_FEATURE", "true")
        assert KayakConfig().editor_feature is True
        monkeypatch.setenv("EDITOR_FEATURE", "0")
        assert KayakConfig().editor_feature is False

    def test_defaults_with_unset_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Wipe relevant env vars to exercise the defaults.
        for k in (
            "DATABASE_URL",
            "OUTPUT_DIR",
            "FETCH_TIMEOUT",
            "FETCH_BUDGET",
            "FETCH_USER_AGENT",
            "MAINTAINER_NAME",
            "MAINTAINER_EMAIL",
            "SITE_URL",
            "EDITOR_FEATURE",
        ):
            monkeypatch.delenv(k, raising=False)
        cfg = KayakConfig()
        assert cfg.fetch_timeout == 300
        assert cfg.fetch_budget == 240
        assert cfg.fetch_user_agent == "kayak/1.0"
        assert cfg.maintainer_name == "Pat Welch"
        assert cfg.maintainer_emails == []
        assert cfg.editor_feature is False
        assert cfg.editor_session_ttl_days == 7
        assert str(cfg.site_url).startswith("https://levels.wkcc.org")
        assert cfg.database_url.startswith("sqlite:///")


class TestKayakConfigValidation:
    """Out-of-range / unparseable inputs raise at construction."""

    def test_invalid_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SITE_URL", "not-a-url")
        with pytest.raises(ValidationError):
            KayakConfig()

    def test_fetch_timeout_below_min_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FETCH_TIMEOUT", "0")
        with pytest.raises(ValidationError):
            KayakConfig()

    def test_fetch_timeout_above_max_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FETCH_TIMEOUT", "601")
        with pytest.raises(ValidationError):
            KayakConfig()

    def test_invalid_email_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAINTAINER_EMAIL", "not-an-email")
        with pytest.raises(ValidationError):
            KayakConfig()

    def test_extra_kwarg_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            KayakConfig(maintainr_email="typo@x.com")  # type: ignore[call-arg]


class TestMaintainerEmailsCsv:
    """``MAINTAINER_EMAIL`` env var parses comma-separated into a list."""

    def test_csv_two_emails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAINTAINER_EMAIL", "a@example.com,b@example.com")
        assert KayakConfig().maintainer_emails == ["a@example.com", "b@example.com"]

    def test_csv_with_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAINTAINER_EMAIL", " a@example.com , b@example.com ")
        assert KayakConfig().maintainer_emails == ["a@example.com", "b@example.com"]

    def test_csv_empty_string_yields_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAINTAINER_EMAIL", "")
        assert KayakConfig().maintainer_emails == []

    def test_csv_single_email_yields_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAINTAINER_EMAIL", "only@example.com")
        assert KayakConfig().maintainer_emails == ["only@example.com"]


class TestLateBinding:
    """Module-level constants are import-time-frozen; KayakConfig() is fresh."""

    def test_module_constants_do_not_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Module-level constant captured the import-time env. Changing
        # the env now doesn't retroactively update the imported name —
        # callers must call KayakConfig() to pick up the change.
        original_database_url = config.DATABASE_URL
        monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/late-bound.db")
        assert original_database_url == config.DATABASE_URL

    def test_fresh_instantiation_picks_up_late_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/late-bound.db")
        assert KayakConfig().database_url == "sqlite:///tmp/late-bound.db"


class TestDotenvPrecedence:
    """``load_dotenv()`` (default ``override=False``) means OS env wins."""

    def test_os_env_wins_over_dotenv_file(self, tmp_path: Path) -> None:
        # Drive a subprocess with a fabricated HOME so that the
        # ``~/.config/kayak/.env`` load path is exercised in isolation.
        # Without the subprocess we'd be fighting our own already-loaded
        # kayak.config module state.
        fake_home = tmp_path / "home"
        env_dir = fake_home / ".config" / "kayak"
        env_dir.mkdir(parents=True)
        (env_dir / ".env").write_text("MAINTAINER_NAME=From-Dotenv\n")

        script = textwrap.dedent("""
            from kayak.config import KayakConfig
            print(KayakConfig().maintainer_name)
        """)

        # OS env set BEFORE the subprocess starts; load_dotenv default
        # (override=False) must NOT clobber it. Pass HOME so Path.home()
        # routes through fake_home for the .env-file load.
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["MAINTAINER_NAME"] = "From-Os-Env"

        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "From-Os-Env"

    def test_dotenv_fills_in_when_os_env_missing(self, tmp_path: Path) -> None:
        # Same setup but no OS env override; .env supplies the value.
        fake_home = tmp_path / "home"
        env_dir = fake_home / ".config" / "kayak"
        env_dir.mkdir(parents=True)
        (env_dir / ".env").write_text("MAINTAINER_NAME=Only-In-Dotenv\n")

        script = textwrap.dedent("""
            from kayak.config import KayakConfig
            print(KayakConfig().maintainer_name)
        """)

        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env.pop("MAINTAINER_NAME", None)

        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "Only-In-Dotenv"
