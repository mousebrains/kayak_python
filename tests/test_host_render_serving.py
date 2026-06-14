"""Tests for the 4C serving renderers (nginx root + FPM open_basedir)."""

from __future__ import annotations

import argparse
from pathlib import Path

from kayak.host import HostConfig
from kayak.host_render import render_fpm_open_basedir, render_nginx_root


def _cutover() -> HostConfig:
    return HostConfig(service_home="/home/pat", docroot="/var/cache/kayak/docroot")


class TestRenderNginxRoot:
    def test_root_is_the_configured_docroot(self) -> None:
        assert render_nginx_root(_cutover()) == "root /var/cache/kayak/docroot;\n"

    def test_default_is_the_live_public_html(self) -> None:
        # keep-current: no host.yaml → the live root, byte-for-byte unchanged.
        assert render_nginx_root(HostConfig()) == "root /home/pat/public_html;\n"


class TestRenderFpmOpenBasedir:
    def test_lists_docroot_runtime_data_and_config(self) -> None:
        out = render_fpm_open_basedir(_cutover())
        assert out == (
            "php_admin_value[open_basedir] = "
            "/var/cache/kayak/docroot:/home/pat/var:/home/pat/DB:/home/pat/logs:"
            "/etc/kayak/runtime-config.json\n"
        )

    def test_no_release_path_after_the_3_docroot_move(self) -> None:
        # The docroot is no longer inside the release, and build copies a
        # self-contained PHP tree into it, so PHP never reads /opt/kayak/* —
        # the pre-#3 open_basedir's release entry is gone.
        assert "/opt/kayak" not in render_fpm_open_basedir(_cutover())

    def test_default_keeps_the_live_public_html_sandbox(self) -> None:
        out = render_fpm_open_basedir(HostConfig())
        assert out.startswith("php_admin_value[open_basedir] = /home/pat/public_html:")

    def test_service_home_flows_through(self) -> None:
        out = render_fpm_open_basedir(HostConfig(service_home="/srv/kayak"))
        assert "/srv/kayak/var:/srv/kayak/DB:/srv/kayak/logs" in out


class TestRenderServingCli:
    def test_writes_both_fragments(self, tmp_path: Path) -> None:
        from kayak.cli import render_serving as cli

        hy = tmp_path / "host.yaml"
        hy.write_text("docroot: /var/cache/kayak/docroot\n")
        rc = cli.render_serving(argparse.Namespace(out_dir=tmp_path / "out", host_config=hy))
        assert rc == 0
        nginx = (tmp_path / "out" / "nginx-levels-docroot.conf").read_text()
        fpm = (tmp_path / "out" / "fpm-open-basedir.conf").read_text()
        assert nginx == "root /var/cache/kayak/docroot;\n"
        assert "open_basedir] = /var/cache/kayak/docroot:" in fpm

    def test_manifest_to_stdout(self, capsys) -> None:
        from kayak.cli import render_serving as cli

        rc = cli.render_serving(argparse.Namespace(out_dir=None, host_config=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "# ==> nginx:" in out and "# ==> php-fpm:" in out
        assert "root /home/pat/public_html;" in out

    def test_malformed_host_config_is_clean_error(self, tmp_path: Path, capsys) -> None:
        from kayak.cli import render_serving as cli

        bad = tmp_path / "host.yaml"
        bad.write_text("docroot: relative/path\n")
        rc = cli.render_serving(argparse.Namespace(out_dir=None, host_config=bad))
        assert rc == 1
        assert "host config invalid" in capsys.readouterr().err
