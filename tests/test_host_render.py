"""Tests for kayak.host_render — the 4C cutover systemd drop-in renderer."""

from __future__ import annotations

import argparse
from pathlib import Path

from kayak.host import HostConfig
from kayak.host_render import render_cutover_dropins


def _cutover_host() -> HostConfig:
    """A fully cut-over host.yaml shape (paths flipped off the install root)."""
    return HostConfig(
        service_user="pat",
        service_home="/home/pat",
        release_root="/opt/kayak",
        docroot="/var/cache/kayak/docroot",
        status_output="/home/pat/var/status.html",
        map_layers_dir="/var/cache/kayak/map-layers",
        gauge_metadata_cache="/var/cache/kayak/gauge-metadata/gauges.db",
    )


def _by_unit(h: HostConfig) -> dict[str, str]:
    return {d.unit: d.text for d in render_cutover_dropins(h)}


class TestRenderCutoverDropins:
    def test_exactly_the_six_engine_consumers(self) -> None:
        units = {d.unit for d in render_cutover_dropins(_cutover_host())}
        assert units == {
            "kayak-pipeline.service",
            "kayak-decimate.service",
            "kayak-editor-retention.service",
            "kayak-fetch-osmb.service",
            "kayak-status.service",
            "kayak-audit-gauges.service",  # the 6th, via #191's promotion
        }

    def test_dropin_path_is_the_systemd_override(self) -> None:
        d = render_cutover_dropins(_cutover_host())[0]
        assert d.path == f"{d.unit}.d/cutover.conf"

    def test_every_dropin_resets_then_repoints_execstart_at_the_release_venv(self) -> None:
        for unit, text in _by_unit(_cutover_host()).items():
            # The empty ExecStart= reset must precede the new one (oneshot would
            # otherwise run both the old and new commands).
            assert "\nExecStart=\nExecStart=/opt/kayak/current/venv/bin/levels " in text, unit
            assert "/home/pat/.venv/bin/levels" not in text, unit

    def test_every_dropin_resets_then_sets_readwritepaths(self) -> None:
        for unit, text in _by_unit(_cutover_host()).items():
            assert "\nReadWritePaths=\nReadWritePaths=" in text, unit

    def test_dataset_dir_pinned_to_release_on_every_unit(self) -> None:
        for unit, text in _by_unit(_cutover_host()).items():
            assert "Environment=DATASET_DIR=/opt/kayak/current/dataset" in text, unit

    def test_pipeline_builds_the_shared_docroot(self) -> None:
        t = _by_unit(_cutover_host())["kayak-pipeline.service"]
        assert "ExecStart=/opt/kayak/current/venv/bin/levels pipeline\n" in t
        assert "Environment=OUTPUT_DIR=/var/cache/kayak/docroot" in t
        assert "ReadWritePaths=/var/cache/kayak/docroot /home/pat/DB" in t

    def test_fetch_osmb_relocates_map_layers_off_the_release(self) -> None:
        t = _by_unit(_cutover_host())["kayak-fetch-osmb.service"]
        assert "Environment=MAP_LAYERS_DIR=/var/cache/kayak/map-layers" in t
        assert "ReadWritePaths=/var/cache/kayak/map-layers" in t

    def test_audit_gauges_relocates_cache_and_keeps_email_var(self) -> None:
        t = _by_unit(_cutover_host())["kayak-audit-gauges.service"]
        assert (
            "ExecStart=/opt/kayak/current/venv/bin/levels "
            "audit-gauges --days 16 --email ${AUDIT_EMAIL}\n" in t
        )
        # The cache file relocates; ReadWritePaths grants its parent DIR + the DB.
        assert "Environment=GAUGE_METADATA_CACHE=/var/cache/kayak/gauge-metadata/gauges.db" in t
        assert "ReadWritePaths=/home/pat/DB /var/cache/kayak/gauge-metadata" in t

    def test_status_passes_output_arg_and_grants_its_dir(self) -> None:
        t = _by_unit(_cutover_host())["kayak-status.service"]
        assert (
            "ExecStart=/opt/kayak/current/venv/bin/levels status --output /home/pat/var/status.html\n"
            in t
        )
        assert "ReadWritePaths=/home/pat/var" in t

    def test_db_only_units_grant_only_the_db(self) -> None:
        units = _by_unit(_cutover_host())
        for u in ("kayak-decimate.service", "kayak-editor-retention.service"):
            assert "ReadWritePaths=\nReadWritePaths=/home/pat/DB\n" in units[u], u
            assert "OUTPUT_DIR" not in units[u], u

    def test_defaults_render_the_current_live_shape(self) -> None:
        # With no host.yaml override the renderer still points at /opt/kayak/current
        # (release_root default) but the relocatable caches keep their live
        # repo-relative values — keep-current-then-flip.
        t = _by_unit(HostConfig())["kayak-fetch-osmb.service"]
        assert "Environment=MAP_LAYERS_DIR=/home/pat/kayak/var/osmb" in t
        g = _by_unit(HostConfig())["kayak-audit-gauges.service"]
        assert (
            "Environment=GAUGE_METADATA_CACHE=/home/pat/kayak/Gauge-metadata-cache/gauges.db" in g
        )

    def test_alternate_service_user_and_home_flow_through(self) -> None:
        h = HostConfig(service_home="/srv/kayak", release_root="/srv/releases")
        t = _by_unit(h)["kayak-decimate.service"]
        assert "ExecStart=/srv/releases/current/venv/bin/levels decimate\n" in t
        assert "ReadWritePaths=/srv/kayak/DB" in t


class TestRenderUnitsCli:
    def test_writes_dropin_files(self, tmp_path: Path) -> None:
        from kayak.cli import render_units as cli

        rc = cli.render_units(argparse.Namespace(out_dir=tmp_path, host_config=None))
        assert rc == 0
        written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
        assert "kayak-pipeline.service.d/cutover.conf" in written
        assert len(written) == 6
        body = (tmp_path / "kayak-pipeline.service.d/cutover.conf").read_text()
        assert "[Service]" in body and "ExecStart=" in body

    def test_manifest_to_stdout_when_no_out_dir(self, capsys) -> None:
        from kayak.cli import render_units as cli

        rc = cli.render_units(argparse.Namespace(out_dir=None, host_config=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "# ==> kayak-pipeline.service.d/cutover.conf" in out

    def test_malformed_host_config_is_clean_error(self, tmp_path: Path, capsys) -> None:
        from kayak.cli import render_units as cli

        bad = tmp_path / "host.yaml"
        bad.write_text("release_root: not-absolute\n")
        rc = cli.render_units(argparse.Namespace(out_dir=None, host_config=bad))
        assert rc == 1
        assert "host config invalid" in capsys.readouterr().err
