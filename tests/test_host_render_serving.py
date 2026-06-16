"""Tests for the 4C serving renderers (nginx root + FPM open_basedir)."""

from __future__ import annotations

import argparse
from pathlib import Path

from kayak.host import HostConfig
from kayak.host_render import render_fpm_open_basedir, render_nginx_root

_REPO = Path(__file__).resolve().parents[1]


def _cutover() -> HostConfig:
    return HostConfig(service_home="/home/pat", docroot="/var/cache/kayak/docroot")


class TestRendererPinnedToCommittedConfig:
    """The renderer is a second source of truth for open_basedir/root; pin it to
    the committed pool/snippet so the sandbox can't silently diverge from what the
    renderer will emit at cutover (PR #194 review #1 — open_basedir REPLACES, so a
    dropped path means PHP silently loses access)."""

    def test_default_open_basedir_matches_committed_pool_byte_for_byte(self) -> None:
        pool = _REPO / "deploy" / "kayak-fpm-pool.conf"
        committed = [
            ln.rstrip("\n")
            for ln in pool.read_text().splitlines()
            if ln.startswith("php_admin_value[open_basedir]")
        ]
        assert len(committed) == 1, "expected exactly one open_basedir in the pool"
        # The default HostConfig is the live WKCC shape, so the renderer must
        # reproduce the committed line exactly — entries, order, ` = ` spacing.
        assert render_fpm_open_basedir(HostConfig()).rstrip("\n") == committed[0]

    def test_default_root_matches_committed_snippet_and_only_the_docroot_one(self) -> None:
        snippet = _REPO / "conf" / "snippets" / "levels-common.conf"
        roots = [
            ln.strip() for ln in snippet.read_text().splitlines() if ln.strip().startswith("root ")
        ]
        # Two root directives exist — the docroot (line ~30) and the ACME
        # `root /var/www/certbot;` (line ~305). The renderer covers only the
        # docroot one; the apply step (increment 4) must not clobber the certbot
        # root (PR #194 review #2). This asserts exactly one non-certbot root.
        docroot_roots = [r for r in roots if "certbot" not in r]
        assert len(docroot_roots) == 1, f"expected one docroot root, got {docroot_roots}"
        assert render_nginx_root(HostConfig()).strip() == docroot_roots[0]

    def test_no_absolute_public_html_static_aliases(self) -> None:
        # favicon / security.txt serve docroot-relative via `try_files /static/...`
        # (cutover follow-up #2), so they follow the rendered root and stay generic.
        # Pin it: no `alias ...public_html...` may creep back — it would 404 once
        # public_html is removed AND diverge from the live file (config-drift does
        # not mask alias lines). PR #199 review #2.
        snippet = _REPO / "conf" / "snippets" / "levels-common.conf"
        offenders = [
            ln.strip()
            for ln in snippet.read_text().splitlines()
            if ln.strip().startswith("alias ") and "public_html" in ln
        ]
        assert not offenders, f"absolute public_html alias(es) reintroduced: {offenders}"


class TestRenderNginxRoot:
    def test_root_is_the_configured_docroot(self) -> None:
        assert render_nginx_root(_cutover()) == "root /var/cache/kayak/docroot;\n"

    def test_default_is_the_live_docroot(self) -> None:
        # keep-current: no host.yaml → the live (post-cutover) shared docroot.
        assert render_nginx_root(HostConfig()) == "root /var/cache/kayak/docroot;\n"


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

    def test_default_keeps_the_live_docroot_sandbox(self) -> None:
        out = render_fpm_open_basedir(HostConfig())
        assert out.startswith("php_admin_value[open_basedir] = /var/cache/kayak/docroot:")

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
        assert "root /var/cache/kayak/docroot;" in out

    def test_malformed_host_config_is_clean_error(self, tmp_path: Path, capsys) -> None:
        from kayak.cli import render_serving as cli

        bad = tmp_path / "host.yaml"
        bad.write_text("docroot: relative/path\n")
        rc = cli.render_serving(argparse.Namespace(out_dir=None, host_config=bad))
        assert rc == 1
        assert "host config invalid" in capsys.readouterr().err
