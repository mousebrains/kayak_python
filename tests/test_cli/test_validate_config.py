"""Tests for ``levels validate-config``."""

from __future__ import annotations

from argparse import Namespace

import pytest

from kayak.cli.validate_config import _known_env_names, validate_config


def _args(**kw: bool) -> Namespace:
    return Namespace(known_env=kw.get("known_env", False), strict=kw.get("strict", False))


class TestKnownEnvNames:
    """The allowlist is derived from KayakConfig's fields + aliases + extras."""

    def test_includes_field_names_uppercased(self) -> None:
        names = _known_env_names()
        assert "DATABASE_URL" in names
        assert "FETCH_TIMEOUT" in names
        assert "EDITOR_FEATURE" in names
        assert "SITE_URL" in names

    def test_includes_validation_alias(self) -> None:
        # `maintainer_emails` field has alias `MAINTAINER_EMAIL` (singular)
        names = _known_env_names()
        assert "MAINTAINER_EMAIL" in names

    def test_includes_reader_overrides(self) -> None:
        names = _known_env_names()
        assert "KAYAK_CONFIG_PATH" in names
        assert "KAYAK_LEVELS_BIN" in names
        assert "KAYAK_HOME" in names


class TestValidateConfig:
    """`validate-config` returns the right exit code per scenario."""

    def test_exits_zero_when_config_is_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            validate_config(_args())
        assert exc.value.code == 0
        assert "OK" in capsys.readouterr().out

    def test_exits_one_when_field_invalid(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # FETCH_TIMEOUT has gt=0; setting 0 triggers ValidationError.
        monkeypatch.setenv("FETCH_TIMEOUT", "0")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args())
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "validation failed" in err.lower()

    def test_known_env_warns_on_unknown_var(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("MAINTAINER_EMIAL", "typo@example.com")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True))
        # WARNs print to stderr but exit 0 (warn-only default).
        assert exc.value.code == 0
        err = capsys.readouterr().err
        assert "MAINTAINER_EMIAL" in err

    def test_known_env_strict_fails_on_unknown_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAINTAINER_EMIAL", "typo@example.com")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True, strict=True))
        assert exc.value.code == 1

    def test_known_env_silent_on_known_var(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Set a real config var; should NOT warn.
        monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True))
        assert exc.value.code == 0
        err = capsys.readouterr().err
        assert "DATABASE_URL" not in err

    def test_known_env_ignores_non_config_prefix(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Random non-config env var — must not be flagged.
        monkeypatch.setenv("XYZZY_RANDOM_VAR", "value")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True))
        assert exc.value.code == 0
        err = capsys.readouterr().err
        assert "XYZZY_RANDOM_VAR" not in err
