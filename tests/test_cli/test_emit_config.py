"""Tests for ``levels emit-config`` and ``levels show-config``."""

from __future__ import annotations

import json
import os
import stat
from argparse import Namespace
from pathlib import Path

import pytest

from kayak.cli.emit_config import (
    build_config_data,
    emit_config,
    show_config,
)
from kayak.config import KayakConfig


@pytest.fixture
def out_path(tmp_path: Path) -> Path:
    """A non-existent JSON path inside an existing tmp dir."""
    return tmp_path / "runtime-config.json"


def _args(out: Path, *, dry_run: bool = False) -> Namespace:
    return Namespace(out=str(out), dry_run=dry_run)


@pytest.fixture(autouse=True)
def _clean_dataset_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASET_DIR", str(tmp_path / "dataset"))
    monkeypatch.delenv("SITE_URL", raising=False)


class TestBuildConfigData:
    """``build_config_data`` produces a JSON-shape dict for KayakConfig."""

    def test_includes_populated_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
        monkeypatch.setenv("FETCH_TIMEOUT", "111")
        data = build_config_data(KayakConfig())
        assert data["database_url"] == "sqlite:///tmp/test.db"
        assert data["fetch_timeout"] == 111
        assert data["fetch_user_agent"] == "kayak/1.0"

    def test_includes_allowed_origins_from_host_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The status.php CORS allow-list is bridged from HostConfig into the JSON
        # PHP reads. With no host.yaml, it's the engine default list.
        from kayak import host

        monkeypatch.delenv("KAYAK_HOST_CONFIG", raising=False)
        host.get_host_config.cache_clear()
        try:
            data = build_config_data(KayakConfig())
            assert data["allowed_origins"] == list(host.HostConfig().allowed_origins)
        finally:
            host.get_host_config.cache_clear()

    def test_includes_site_identity_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # S3a: PHP reads site identity from the same resolved block. With no
        # dataset site.yaml the generic engine defaults are emitted.
        monkeypatch.setenv("DATASET_DIR", str(tmp_path))  # empty dataset dir → defaults
        data = build_config_data(KayakConfig())
        assert data["site"]["site_name"] == "River Levels"
        assert data["site"]["org_label"] == "Kayak"
        assert data["site"]["brand_color"] == "#1b5591"
        assert data["data_license"]["label"] == "CC BY-NC 4.0"
        assert data["data_license"]["url"] == "https://creativecommons.org/licenses/by-nc/4.0/"

    def test_site_identity_reflects_dataset_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "site.yaml").write_text('site_name: Foo Levels\nbrand_color: "#abcdef"\n')
        monkeypatch.setenv("DATASET_DIR", str(tmp_path))
        data = build_config_data(KayakConfig())
        assert data["site"]["site_name"] == "Foo Levels"
        assert data["site"]["brand_color"] == "#abcdef"
        assert data["site"]["org_name"] == "Kayak"  # default kept

    def test_data_license_reflects_dataset_contract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "dataset.yaml").write_text(
            "contract_version: 1\n"
            "dataset_id: test\n"
            "name: Test Levels\n"
            "status: scaffold\n"
            "license: CC0-1.0\n"
            'engine_test_ref: "0000000000000000000000000000000000000000"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("DATASET_DIR", str(tmp_path))
        data = build_config_data(KayakConfig())
        assert data["data_license"] == {
            "id": "CC0-1.0",
            "label": "CC0 1.0",
            "notice": (
                "Metadata + calculated values: CC0 1.0. Observations: public domain at source."
            ),
            "url": "https://creativecommons.org/publicdomain/zero/1.0/",
        }

    def test_data_license_manifest_error_is_clean_runtime_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "dataset.yaml").write_text(
            "contract_version: 1\n"
            "dataset_id: test\n"
            "name: Test Levels\n"
            "status: scaffold\n"
            "license: ''\n"
            'engine_test_ref: "0000000000000000000000000000000000000000"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("DATASET_DIR", str(tmp_path))
        with pytest.raises(RuntimeError, match="license must be a non-empty string"):
            build_config_data(KayakConfig())

    def test_excludes_none_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Strip all hc_*, mail_*, turnstile_* env vars so they default to None.
        for key in list(os.environ):
            if key.startswith(("HC_", "MAIL_", "TURNSTILE_", "NTFY_")):
                monkeypatch.delenv(key, raising=False)
        data = build_config_data(KayakConfig())
        assert "hc_pipeline" not in data
        assert "mail_from" not in data
        assert "turnstile_secret" not in data

    def test_secret_str_unwrapped_to_plaintext(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TURNSTILE_SECRET", "real-secret-not-masked")
        data = build_config_data(KayakConfig())
        # The plaintext lands in the JSON because the runtime-config.json
        # file is mode 0640 root:www-data — root and PHP-FPM only.
        assert data["turnstile_secret"] == "real-secret-not-masked"
        assert "*" not in data["turnstile_secret"]

    def test_exclude_secrets_drops_every_secretstr_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Type-based, not name-based (PR #190 live review): every SecretStr
        # field is dropped from the non-secret copy regardless of its name, so
        # a future secret field can't leak into a retained release config.
        from pydantic import SecretStr

        monkeypatch.setenv("TURNSTILE_SECRET", "real-secret-not-masked")
        cfg = KayakConfig()
        # Detect secret fields the same way build_config_data does — by the
        # populated instance value's type, not the (Optional-wrapped) annotation.
        secret_fields = [
            n for n in KayakConfig.model_fields if isinstance(getattr(cfg, n), SecretStr)
        ]
        assert "turnstile_secret" in secret_fields, "guard: turnstile_secret must be a SecretStr"
        data = build_config_data(cfg, exclude_secrets=True)
        for name in secret_fields:
            assert name not in data, name
        assert "real-secret-not-masked" not in json.dumps(data)

    def test_database_path_derived_from_sqlite_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # SQLAlchemy SQLite URLs use 3 slashes for relative paths and 4
        # for absolute (``sqlite:////home/...``). Strip exactly the 3-slash
        # prefix; the 4th slash that introduces an absolute path stays.
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/kayak-test.db")
        data = build_config_data(KayakConfig())
        assert data["database_path"] == "/tmp/kayak-test.db"
        assert data["database_url"] == "sqlite:////tmp/kayak-test.db"

    def test_database_path_omitted_for_non_sqlite_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Future-proof: postgres etc. shouldn't grow a misleading
        # database_path field. The derivation only fires for sqlite:///.
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/kayak")
        data = build_config_data(KayakConfig())
        assert "database_path" not in data
        assert data["database_url"] == "postgresql://localhost/kayak"

    def test_publishable_dataset_requires_explicit_site_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        with pytest.raises(RuntimeError, match="SITE_URL must be set explicitly"):
            build_config_data(KayakConfig())


class TestEmitConfig:
    """``levels emit-config`` writes JSON atomically + idempotently."""

    def test_writes_to_out_path(self, out_path: Path) -> None:
        emit_config(_args(out_path))
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert "database_url" in data
        assert "fetch_timeout" in data

    def test_writes_mode_0640(self, out_path: Path) -> None:
        emit_config(_args(out_path))
        mode = stat.S_IMODE(out_path.stat().st_mode)
        assert mode == 0o640

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "etc" / "kayak" / "runtime-config.json"
        emit_config(_args(nested))
        assert nested.exists()

    def test_idempotent_second_run_unchanged(
        self, out_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        emit_config(_args(out_path))
        first_mtime = out_path.stat().st_mtime_ns
        capsys.readouterr()  # discard first run's output

        emit_config(_args(out_path))
        out = capsys.readouterr().out
        assert "unchanged" in out
        assert out_path.stat().st_mtime_ns == first_mtime

    def test_run_after_env_change_writes_updated(
        self,
        out_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("FETCH_TIMEOUT", "300")
        emit_config(_args(out_path))
        capsys.readouterr()

        monkeypatch.setenv("FETCH_TIMEOUT", "240")
        emit_config(_args(out_path))
        out = capsys.readouterr().out
        assert "updated" in out
        assert json.loads(out_path.read_text())["fetch_timeout"] == 240

    def test_dry_run_writes_to_stdout(
        self, out_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        emit_config(_args(out_path, dry_run=True))
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert "database_url" in data
        assert not out_path.exists()

    def test_atomic_temp_not_left_behind(self, out_path: Path) -> None:
        emit_config(_args(out_path))
        # The .tmp sibling must be renamed-away, not left.
        tmp = out_path.with_name(out_path.name + ".tmp")
        assert not tmp.exists()

    def test_no_trailing_garbage(self, out_path: Path) -> None:
        # JSON ends with exactly one newline (stable serialization).
        emit_config(_args(out_path))
        content = out_path.read_text()
        assert content.endswith("}\n")
        assert not content.endswith("}\n\n")

    def test_publishable_dataset_without_site_url_exits_cleanly(
        self,
        tmp_path: Path,
        out_path: Path,
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
            emit_config(_args(out_path))
        assert exc.value.code == 1
        assert "SITE_URL must be set explicitly" in capsys.readouterr().err
        assert not out_path.exists()


class TestShowConfig:
    """``levels show-config`` prints to stdout in either format."""

    def test_table_format_prints_each_field(self, capsys: pytest.CaptureFixture[str]) -> None:
        show_config(Namespace(format="table"))
        out = capsys.readouterr().out
        assert "database_url" in out
        assert "fetch_timeout" in out
        # ``(unset)`` placeholder is emitted for None fields by default.
        assert "(unset)" in out

    def test_table_format_renders_empty_list_marker(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("MAINTAINER_EMAIL", raising=False)
        show_config(Namespace(format="table"))
        out = capsys.readouterr().out
        assert "(empty list)" in out

    def test_json_format_is_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        show_config(Namespace(format="json"))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "database_url" in data
        assert "fetch_timeout" in data
