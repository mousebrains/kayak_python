"""Unit tests for kayak.host — the typed host configuration (S7/S8, Batch 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak import host


class TestLoadHostConfig:
    def test_absent_returns_current_defaults(self, tmp_path: Path) -> None:
        # Behavior-neutral rollout: until the live host ships an explicit
        # host.yaml, the defaults are the CURRENT production values (the
        # generic flip is the Batch 4C cutover step, mirroring S3).
        c = host.load_host_config(tmp_path / "host.yaml")
        assert c.timezone == "America/Los_Angeles"
        assert c.nginx_log_glob == "/var/log/nginx/levels-*.access.log*"
        assert c.status_output == "/home/pat/var/status.html"
        assert c.docroot == "/home/pat/public_html"
        assert c.cert_host == "levels.wkcc.org"
        assert c.backup_dir == "/home/pat/backups"
        assert c.offsite_remote == "gdrive-crypt"
        assert c.offsite_keep == 26
        assert c.offsite_label == "rclone → gdrive-crypt:"

    def test_overrides_applied_partially(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text(
            "timezone: America/Denver\n"
            "cert_host: levels.example.org\n"
            "backup_dir: /var/lib/kayak/backups\n"
            "offsite_remote: b2-crypt\n"
            "offsite_keep: 12\n"
        )
        c = host.load_host_config(f)
        assert c.timezone == "America/Denver"
        assert c.cert_host == "levels.example.org"
        assert c.backup_dir == "/var/lib/kayak/backups"
        assert c.offsite_label == "rclone → b2-crypt:"
        assert c.docroot == "/home/pat/public_html"  # untouched default

    def test_env_var_names_the_file(self, tmp_path: Path, monkeypatch) -> None:
        f = tmp_path / "elsewhere.yaml"
        f.write_text("timezone: UTC\n")
        monkeypatch.setenv("KAYAK_HOST_CONFIG", str(f))
        assert host.load_host_config().timezone == "UTC"

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("bogus_knob: 1\n")
        with pytest.raises(ValueError, match=r"bogus_knob|[Ee]xtra"):
            host.load_host_config(f)

    def test_bad_timezone_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("timezone: Pacific Standard Time\n")
        with pytest.raises(ValueError, match="IANA"):
            host.load_host_config(f)

    def test_relative_path_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("backup_dir: backups\n")
        with pytest.raises(ValueError, match="absolute"):
            host.load_host_config(f)

    def test_colon_in_remote_rejected(self, tmp_path: Path) -> None:
        # The colon is rclone syntax appended by consumers; a configured
        # colon would silently change the remote path semantics.
        f = tmp_path / "host.yaml"
        f.write_text("offsite_remote: 'gdrive-crypt:'\n")
        with pytest.raises(ValueError, match="colon"):
            host.load_host_config(f)

    def test_bad_cert_host_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("cert_host: 'https://levels.wkcc.org'\n")
        with pytest.raises(ValueError, match="hostname"):
            host.load_host_config(f)

    def test_zero_offsite_keep_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("offsite_keep: 0\n")
        with pytest.raises(ValueError, match="at least 1"):
            host.load_host_config(f)

    def test_malformed_yaml_fails_closed(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("timezone: [unclosed\n")
        with pytest.raises(ValueError, match="malformed"):
            host.load_host_config(f)

    def test_non_mapping_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="mapping"):
            host.load_host_config(f)

    def test_empty_file_is_defaults(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("")
        assert host.load_host_config(f).offsite_keep == 26


class TestLazyHostConfigLoading:
    def test_cli_parser_builds_with_malformed_host_yaml(self, tmp_path: Path) -> None:
        """A malformed host.yaml must not crash unrelated commands: main.py
        registers every subcommand's addArgs per invocation, so addArgs must
        not load host.yaml (PR #189 review P2)."""
        import os
        import subprocess
        import sys

        bad = tmp_path / "host.yaml"
        bad.write_text("timezone: [broken\n")
        proc = subprocess.run(
            [sys.executable, "-m", "kayak.cli.main", "init-db", "--help"],
            env={**os.environ, "KAYAK_HOST_CONFIG": str(bad)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr

    def test_status_run_reports_bad_host_config_cleanly(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        import argparse

        from kayak.cli import status as status_mod

        def _boom() -> host.HostConfig:
            raise ValueError("host.yaml: malformed")

        monkeypatch.setattr(status_mod, "get_host_config", _boom)
        rc = status_mod.run(
            argparse.Namespace(output=None, hours=24, bucket_hours=4, tz=None, log_glob=None)
        )
        assert rc == 1
        assert "host config invalid" in capsys.readouterr().err

    def test_status_run_resolves_none_defaults_from_host_config(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """--output/--tz/--log-glob default to None at parse time and resolve
        from HostConfig inside run()."""
        import argparse

        from kayak.cli import status as status_mod

        cfg = host.HostConfig(status_output=str(tmp_path / "out.html"), timezone="UTC")
        monkeypatch.setattr(status_mod, "get_host_config", lambda: cfg)
        seen: dict[str, object] = {}

        def _fake_render(**kw: object) -> str:
            seen.update(kw)
            return "<html>ok</html>"

        monkeypatch.setattr(status_mod, "_render_page", _fake_render)
        rc = status_mod.run(
            argparse.Namespace(output=None, hours=24, bucket_hours=4, tz=None, log_glob=None)
        )
        assert rc == 0
        assert (tmp_path / "out.html").read_text() == "<html>ok</html>"
        assert str(seen["tz"]) == "UTC"
        assert seen["log_glob"] == cfg.nginx_log_glob


class TestStatusConsumesHostConfig:
    def test_backups_cert_section_uses_host_values(self, tmp_path: Path, monkeypatch) -> None:
        """The status page's backup/cert section renders the configured
        offsite label and certificate host (S7 'parameterize status checks')."""
        import datetime as dt

        from kayak.cli import status as status_mod

        cfg = host.HostConfig(
            cert_host="levels.example.org",
            offsite_remote="b2-crypt",
            backup_dir=str(tmp_path),
        )
        monkeypatch.setattr(status_mod, "get_host_config", lambda: cfg)
        # No systemd/openssl on the test box: stub the probes.
        monkeypatch.setattr(status_mod, "_show_unit", lambda *_a, **_k: {})
        monkeypatch.setattr(status_mod, "_cert_not_after", lambda h: None)

        out = status_mod._render_backups_cert(dt.datetime.now(dt.UTC))
        assert "rclone → b2-crypt:" in out
        assert "levels.example.org" in out
        # The row LABELS name the directory actually probed (PR #189 review
        # P2: a configured backup_dir must not be reported as ~/backups).
        assert f"Hourly backup ({tmp_path}/hourly-*.db.gz)" in out
        assert f"Weekly backup ({tmp_path}/backup-*.db.gz)" in out
        assert "~/backups" not in out
        assert "gdrive-crypt" not in out
        assert "levels.wkcc.org" not in out
