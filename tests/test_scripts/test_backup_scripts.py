"""Fail-closed knob guards in the backup shell scripts (S8, PR #189 review P1).

The guards sit BEFORE any rclone copy/delete and before ``mapfile``, so these
tests run on any bash (including the dev Mac's 3.2): a bad knob must abort the
script without invoking rclone at all — KEEP=0 reaching the prune loop would
delete every offsite backup.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_OFFSITE = _REPO / "systemd" / "kayak-backup-offsite.sh"
_HOURLY = _REPO / "systemd" / "kayak-backup-hourly.sh"


def _run_with_rclone_shim(
    script: Path, env_overrides: dict[str, str], tmp_path: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run *script* with a PATH-front rclone shim that records invocations."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    calls = tmp_path / "rclone-calls.log"
    shim = shim_dir / "rclone"
    shim.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 0\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        **env_overrides,
        "PATH": f"{shim_dir}:{os.environ['PATH']}",
        # Keep the script from sourcing a real host's /etc/kayak/env values
        # over the test's: the overrides land AFTER the source line via env.
        "KAYAK_HOME": str(tmp_path),
    }
    proc = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=30
    )
    return proc, calls


# (empty string is NOT here: ${VAR:-default} treats empty as unset -> 26.)
@pytest.mark.parametrize("bad_keep", ["0", "-3", "abc", "1.5"])
def test_offsite_aborts_on_bad_keep_before_any_rclone(bad_keep, tmp_path: Path) -> None:
    proc, calls = _run_with_rclone_shim(_OFFSITE, {"KAYAK_OFFSITE_KEEP": bad_keep}, tmp_path)
    assert proc.returncode != 0
    assert "positive integer" in proc.stderr
    assert not calls.exists(), "no rclone invocation may precede the guard"


def test_offsite_aborts_on_colon_remote(tmp_path: Path) -> None:
    proc, calls = _run_with_rclone_shim(
        _OFFSITE, {"KAYAK_OFFSITE_REMOTE": "gdrive-crypt:"}, tmp_path
    )
    assert proc.returncode != 0
    assert "no colon" in proc.stderr
    assert not calls.exists()


def test_offsite_aborts_on_relative_backup_dir(tmp_path: Path) -> None:
    proc, calls = _run_with_rclone_shim(
        _OFFSITE, {"KAYAK_BACKUP_DIR": "relative/backups"}, tmp_path
    )
    assert proc.returncode != 0
    assert "absolute path" in proc.stderr
    assert not calls.exists()


def test_hourly_aborts_on_relative_backup_dir(tmp_path: Path) -> None:
    proc, _ = _run_with_rclone_shim(_HOURLY, {"KAYAK_BACKUP_DIR": "relative/backups"}, tmp_path)
    assert proc.returncode != 0
    assert "absolute path" in proc.stderr
