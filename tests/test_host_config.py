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
        assert c.docroot == "/var/cache/kayak/docroot"
        assert c.cert_host == "levels.wkcc.org"
        assert c.backup_dir == "/home/pat/backups"
        assert c.offsite_remote == "gdrive-crypt"
        assert c.offsite_keep == 26
        assert c.offsite_label == "rclone → gdrive-crypt:"
        # 4C renderer fields default to the current WKCC shape.
        assert c.service_user == "pat"
        assert c.service_home == "/home/pat"
        assert c.release_root == "/opt/kayak"
        assert c.fpm_pool_php == "8.4"
        assert c.map_layers_dir == "/home/pat/kayak/var/osmb"
        assert c.gauge_metadata_cache == "/home/pat/kayak/Gauge-metadata-cache/gauges.db"

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
        assert c.docroot == "/var/cache/kayak/docroot"  # untouched default

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

    def test_renderer_fields_override(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text(
            "service_user: kayak\n"
            "service_home: /srv/kayak\n"
            "release_root: /srv/kayak/releases\n"
            "fpm_pool_php: '8.3'\n"
            "docroot: /var/cache/kayak/docroot\n"
            "map_layers_dir: /var/cache/kayak/map-layers\n"
            "gauge_metadata_cache: /var/cache/kayak/gauge-metadata/gauges.db\n"
        )
        c = host.load_host_config(f)
        assert c.service_user == "kayak"
        assert c.service_home == "/srv/kayak"
        assert c.release_root == "/srv/kayak/releases"
        assert c.fpm_pool_php == "8.3"
        assert c.docroot == "/var/cache/kayak/docroot"
        assert c.map_layers_dir == "/var/cache/kayak/map-layers"
        assert c.gauge_metadata_cache == "/var/cache/kayak/gauge-metadata/gauges.db"

    def test_bad_service_user_rejected(self, tmp_path: Path) -> None:
        # Renderers interpolate it into unit User= and shell ACL commands.
        f = tmp_path / "host.yaml"
        f.write_text("service_user: 'pat; rm -rf /'\n")
        with pytest.raises(ValueError, match="POSIX username"):
            host.load_host_config(f)

    def test_relative_release_root_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("release_root: opt/kayak\n")
        with pytest.raises(ValueError, match="absolute"):
            host.load_host_config(f)

    def test_bad_fpm_version_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "host.yaml"
        f.write_text("fpm_pool_php: '8.4.3'\n")
        with pytest.raises(ValueError, match=r"major\.minor"):
            host.load_host_config(f)

    def test_service_user_trailing_newline_rejected(self) -> None:
        # `\Z` not `$`: a trailing newline would otherwise smuggle a second
        # directive into a rendered unit / ACL command (PR #193 review #2).
        with pytest.raises(ValueError, match="POSIX username"):
            host.HostConfig(service_user="pat\n")

    def test_fpm_version_unicode_digit_rejected(self) -> None:
        # `[0-9]` not `\d` — \d matches Unicode digits the path can't contain.
        with pytest.raises(ValueError, match=r"major\.minor"):
            host.HostConfig(fpm_pool_php="8.٤")  # Arabic-Indic 4

    def test_path_with_whitespace_rejected(self) -> None:
        # Path fields are f-string-interpolated into systemd directives; a space
        # splits a ReadWritePaths= entry, a newline injects a directive.
        with pytest.raises(ValueError, match="whitespace, control"):
            host.HostConfig(docroot="/var/cache/kayak/docroot\nExecStartPre=/bin/x")
        with pytest.raises(ValueError, match="whitespace, control"):
            host.HostConfig(release_root="/opt/kayak extra")

    def test_path_with_config_delimiter_rejected(self) -> None:
        # ':' would silently widen PHP's colon-delimited open_basedir; ';' ends an
        # nginx directive early (PR #194 review #3).
        with pytest.raises(ValueError, match="delimiters"):
            host.HostConfig(docroot="/var/cache/kayak/docroot:/etc")
        with pytest.raises(ValueError, match="delimiters"):
            host.HostConfig(status_output="/home/pat/var/status.html;root /evil")

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
