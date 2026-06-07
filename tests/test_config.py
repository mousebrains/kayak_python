"""Tests for kayak.config defaults and the KayakConfig typed model."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from kayak import config
from kayak.config import KayakConfig

# Subprocess-based tests below spawn a fresh interpreter that must import
# ``kayak`` without an editable install — put ``src`` on its PYTHONPATH so the
# suite passes from a bare checkout (CI installs the package, but a clean
# ``pytest`` shouldn't depend on that). See review-3 R4.2.
_SRC = str(Path(__file__).resolve().parents[1] / "src")


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

    def test_env_override_osmb_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OSMB_DIR", "/srv/osmb")
        assert str(KayakConfig().osmb_dir) == "/srv/osmb"

    def test_osmb_dir_default_is_outside_the_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The OSMB staging dir holds generated runtime data, so its default is a
        # BASE_DIR-relative dev default (like output_dir/metadata_dir), NOT under
        # the packaged engine resources — a wheel's package dir may be read-only.
        monkeypatch.delenv("OSMB_DIR", raising=False)
        osmb = KayakConfig().osmb_dir
        assert osmb.parts[-2:] == ("var", "osmb")
        assert "site-packages/kayak" not in str(osmb)
        assert str(config.DATA_DIR) not in str(osmb)

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
            "DATASET_DIR",
            "METADATA_DIR",
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


class TestDatasetDirRoot:
    """S6.1: DATASET_DIR is the dataset root; METADATA_DIR is a deprecated alias
    honored for one release (warn-only), with a fail-fast when both are set to
    different paths. Value resolution (AliasChoices) is separate from the
    deprecation policy (``_check_dataset_dir_env``)."""

    def test_dataset_dir_env_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("METADATA_DIR", raising=False)
        monkeypatch.setenv("DATASET_DIR", "/srv/ds")
        assert str(KayakConfig().dataset_dir) == "/srv/ds"

    def test_metadata_dir_alias_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASET_DIR", raising=False)
        monkeypatch.setenv("METADATA_DIR", "/srv/legacy")
        assert str(KayakConfig().dataset_dir) == "/srv/legacy"

    def test_dataset_dir_wins_over_metadata_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASET_DIR", "/srv/new")
        monkeypatch.setenv("METADATA_DIR", "/srv/new")  # agree, so no fail-fast
        assert str(KayakConfig().dataset_dir) == "/srv/new"

    def test_metadata_only_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASET_DIR", raising=False)
        monkeypatch.setenv("METADATA_DIR", "/srv/legacy")
        with pytest.warns(DeprecationWarning, match="METADATA_DIR is deprecated"):
            config._check_dataset_dir_env()

    def test_dataset_dir_emits_no_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASET_DIR", "/srv/ds")
        monkeypatch.delenv("METADATA_DIR", raising=False)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            config._check_dataset_dir_env()  # must not warn

    def test_both_set_disagree_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASET_DIR", "/srv/a")
        monkeypatch.setenv("METADATA_DIR", "/srv/b")
        with pytest.raises(ValueError, match="Both DATASET_DIR and METADATA_DIR"):
            config._check_dataset_dir_env()

    def test_both_set_agree_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASET_DIR", "/srv/same")
        monkeypatch.setenv("METADATA_DIR", "/srv/same")
        config._check_dataset_dir_env()  # neither raises nor warns


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
        env["PYTHONPATH"] = _SRC
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

    def test_sudo_user_fallback_when_root_home_lacks_dotenv(self, tmp_path: Path) -> None:
        # Simulate ``sudo -n levels emit-config``: HOME=/root (no .env there),
        # SUDO_USER=pat (whose home DOES have .env). The fallback path must
        # find pat's .env so emit-config writes the operator's live values.
        root_home = tmp_path / "root"
        root_home.mkdir()
        operator_home = tmp_path / "operator"
        env_dir = operator_home / ".config" / "kayak"
        env_dir.mkdir(parents=True)
        (env_dir / ".env").write_text("MAINTAINER_NAME=From-Sudo-Fallback\n")

        # The subprocess script monkey-patches pwd.getpwnam to point our
        # synthetic SUDO_USER at the temp home, since pwd.getpwnam('test-op')
        # would otherwise fail on a real system.
        script = textwrap.dedent(f"""
            import pwd
            class _Pw:
                pw_dir = {str(operator_home)!r}
            real = pwd.getpwnam
            pwd.getpwnam = lambda name: _Pw() if name == 'test-op' else real(name)
            from kayak.config import KayakConfig
            print(KayakConfig().maintainer_name)
        """)

        env = os.environ.copy()
        env["PYTHONPATH"] = _SRC
        env["HOME"] = str(root_home)
        env["SUDO_USER"] = "test-op"
        env.pop("MAINTAINER_NAME", None)

        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "From-Sudo-Fallback"

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
        env["PYTHONPATH"] = _SRC
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
