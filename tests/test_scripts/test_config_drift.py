"""Unit tests for `scripts/check-config-drift.sh`'s `normalize_rendered`.

The drift check masks the two 4C-cutover-rendered lines (the served-docroot nginx
`root` and the PHP-FPM `open_basedir`) before comparing, so a cut-over host's live
files stop showing false drift. The subtlety worth locking down (PR #198 review
#4): the mask must be SURGICAL — it must exempt the *docroot* `root` while leaving
the ACME `root /var/www/certbot;` byte-checked, so a tampered ACME root is still
caught. These tests source the script in lib mode (``KAYAK_DRIFT_LIB=1``) and drive
``normalize_rendered`` directly, so a future ``sed`` edit that breaks the certbot
exclusion fails here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-config-drift.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def _normalize(text: str, tmp_path: Path) -> str:
    """Run the script's ``normalize_rendered`` on *text* via sourcing (lib mode)."""
    fixture = tmp_path / "in.conf"
    fixture.write_text(text, encoding="utf-8")
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'KAYAK_DRIFT_LIB=1 REPO={REPO} source "{SCRIPT}"; normalize_rendered "{fixture}"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def test_docroot_root_is_masked_but_acme_root_preserved(tmp_path: Path) -> None:
    out = _normalize("root /var/cache/kayak/docroot;\n    root /var/www/certbot;\n", tmp_path)
    assert "root @@RENDERED@@;" in out  # the served docroot root is masked
    assert "root /var/www/certbot;" in out  # the ACME root is NOT masked (still drift-checked)


def test_docroot_root_value_change_is_exempt(tmp_path: Path) -> None:
    # Two different docroots normalize to the same line -> no false drift.
    pre = _normalize("root /home/pat/public_html;\n", tmp_path)
    post = _normalize("root /var/cache/kayak/docroot;\n", tmp_path)
    assert pre == post


def test_open_basedir_value_change_is_exempt(tmp_path: Path) -> None:
    pre = _normalize(
        "php_admin_value[open_basedir] = /home/pat/public_html:/home/pat/DB\n", tmp_path
    )
    post = _normalize(
        "php_admin_value[open_basedir] = /var/cache/kayak/docroot:/home/pat/DB\n", tmp_path
    )
    assert pre == post


def test_tampered_acme_root_still_differs(tmp_path: Path) -> None:
    # The certbot root is byte-checked, so tampering it must survive normalization
    # as a difference (a real regression is still caught).
    clean = _normalize("    root /var/www/certbot;\n", tmp_path)
    tampered = _normalize("    root /var/www/evil;\n", tmp_path)
    assert clean != tampered


def test_unrelated_line_is_untouched(tmp_path: Path) -> None:
    # Only the two rendered directives are masked; everything else is verbatim.
    line = "add_header X-Frame-Options DENY;\n"
    assert _normalize(line, tmp_path) == line
