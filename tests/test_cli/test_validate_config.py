"""Tests for ``levels validate-config``."""

from __future__ import annotations

from argparse import Namespace

import pytest

from kayak.cli.validate_config import _known_env_names, validate_config


def _args(**kw: bool) -> Namespace:
    return Namespace(known_env=kw.get("known_env", False), strict=kw.get("strict", False))


@pytest.fixture(autouse=True)
def _clean_dataset_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASET_DIR", str(tmp_path / "dataset"))
    monkeypatch.delenv("METADATA_DIR", raising=False)
    monkeypatch.delenv("SITE_URL", raising=False)


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

    def test_includes_dataset_dir_and_deploy_extras(self) -> None:
        # DATASET_DIR (and its deprecated alias METADATA_DIR) is the data-repo
        # pointer, read from the dataset_dir field's AliasChoices; KAYAK_DATA is
        # the deploy.sh export it's derived from; KAYAK_VENV is the
        # regenerate_schema_svg.sh dev override. All must be known or
        # `deploy.sh`'s --known-env --strict run would fail the deploy. This also
        # guards the _known_env_names() AliasChoices extraction (S6.1).
        names = _known_env_names()
        assert "DATASET_DIR" in names
        assert "METADATA_DIR" in names  # deprecated alias, honored one release
        assert "KAYAK_DATA" in names
        assert "KAYAK_VENV" in names

    def test_includes_map_layers_dir_aliases(self) -> None:
        names = _known_env_names()
        assert "MAP_LAYERS_DIR" in names
        assert "OSMB_DIR" in names

    def test_includes_systemd_heartbeat_urls(self) -> None:
        # Every ${HC_*} referenced by a systemd unit must be a declared
        # field — these two were missed when their units were added.
        names = _known_env_names()
        assert "HC_FETCH_OSMB" in names
        assert "HC_STATUS" in names

    def test_includes_retired_metadata_snapshot_hc(self) -> None:
        # HC_METADATA_SNAPSHOT was retired with the kayak-metadata-snapshot unit
        # (SA-teardown-B); it stays allowlisted for one release so a stale `.env`
        # line can't fail deploy.sh's `--known-env --strict` gate.
        assert "HC_METADATA_SNAPSHOT" in _known_env_names()

    def test_includes_usgs_api_key(self) -> None:
        # Read via os.environ by the OGC fetch (not a model field — the
        # secret must stay out of the www-data-readable config JSON),
        # but set in prod's .env, so strict mode must know it.
        assert "USGS_API_KEY" in _known_env_names()

    def test_includes_sqlite_path(self) -> None:
        # PHP db.php fallback + health-check.sh DB override; set in
        # prod's .env. Not a model field (python uses DATABASE_URL),
        # so strict mode must know the exact name (PR #119 review).
        assert "SQLITE_PATH" in _known_env_names()

    def test_known_env_warns_on_sqlite_typo(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("SQLITE_PTAH", "/tmp/kayak.db")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True))
        assert exc.value.code == 0
        assert "SQLITE_PTAH" in capsys.readouterr().err

    def test_known_env_warns_on_map_layers_typo(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("MAP_LAYERS_DRI", "/tmp/map-layers")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True))
        assert exc.value.code == 0
        assert "MAP_LAYERS_DRI" in capsys.readouterr().err


class TestValidateConfig:
    """`validate-config` returns the right exit code per scenario."""

    def test_exits_zero_when_config_is_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            validate_config(_args())
        assert exc.value.code == 0
        assert "OK" in capsys.readouterr().out

    def test_fails_for_publishable_dataset_without_explicit_site_url(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "dataset.yaml").write_text(
            "contract_version: 1\n"
            "dataset_id: test\n"
            "name: Test Levels\n"
            "status: publishable\n"
            "license: CC-BY-NC-4.0\n"
            'engine_test_ref: "0000000000000000000000000000000000000000"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("DATASET_DIR", str(tmp_path))
        monkeypatch.delenv("SITE_URL", raising=False)
        with pytest.raises(SystemExit) as exc:
            validate_config(_args())
        assert exc.value.code == 1
        assert "SITE_URL must be set explicitly" in capsys.readouterr().err

    def test_passes_for_publishable_dataset_with_explicit_site_url(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "dataset.yaml").write_text(
            "contract_version: 1\n"
            "dataset_id: test\n"
            "name: Test Levels\n"
            "status: publishable\n"
            "license: CC-BY-NC-4.0\n"
            'engine_test_ref: "0000000000000000000000000000000000000000"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("DATASET_DIR", str(tmp_path))
        monkeypatch.setenv("SITE_URL", "https://levels.wkcc.org")
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

    def test_known_env_strict_tolerates_retired_metadata_snapshot_hc(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A stale HC_METADATA_SNAPSHOT left in a prod .env (the unit was retired
        # in SA-teardown-B) must NOT fail the strict deploy gate — it's
        # allowlisted for one release so it can't brick an otherwise-safe deploy.
        monkeypatch.setenv("HC_METADATA_SNAPSHOT", "https://hc-ping.com/example")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True, strict=True))
        assert exc.value.code == 0
        assert "HC_METADATA_SNAPSHOT" not in capsys.readouterr().err

    def test_known_env_warns_on_metadata_typo(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The METADATA_ prefix is still scanned during the DATASET_DIR
        # deprecation window (gpt-5.5 review): a same-prefix typo of the
        # (now-deprecated) METADATA_DIR alias must still be flagged.
        monkeypatch.setenv("METADATA_DRI", "/tmp/kayak_data")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True))
        assert exc.value.code == 0
        assert "METADATA_DRI" in capsys.readouterr().err

    def test_known_env_warns_on_usgs_typo_but_not_real_key(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # USGS_API_KEY drives the hourly OGC fetch via os.environ; a
        # same-prefix typo silently degrades it, so the scanner must
        # flag the typo while accepting the real name.
        monkeypatch.setenv("USGS_API_KEY", "real")
        monkeypatch.setenv("USGS_APIKEY", "typo")
        with pytest.raises(SystemExit) as exc:
            validate_config(_args(known_env=True))
        assert exc.value.code == 0
        err = capsys.readouterr().err
        assert "USGS_APIKEY" in err
        assert "USGS_API_KEY " not in err  # trailing space: exact-name check

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
