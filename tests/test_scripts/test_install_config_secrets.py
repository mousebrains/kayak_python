"""Regression tests for ``deploy/install-config.sh --check-secrets``.

PR #119 review (2026-06-03): the installer's sanity check only enforced
a non-empty ``TURNSTILE_SECRET``, so a fresh host could pass the
installer with the site key missing — and since ``turnstile_enabled()``
requires BOTH JSON keys, the deploy would land with captcha silently
off (the exact failure mode the secrets-merge fix closes). The guard
now requires both keys and parses with the same semantics as the
install wrapper's merge; the ``--check-secrets`` mode runs it without
root so these tests can exercise the real script.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY / "install-config.sh"
WRAPPER = DEPLOY / "kayak-install-runtime-config.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="install-config.sh needs the bash CLI"
)


def _check(tmp_path: Path, content: str | None) -> subprocess.CompletedProcess[str]:
    if content is None:
        target = tmp_path / "no-such-secrets.env"
    else:
        target = tmp_path / "secrets.env"
        target.write_text(content)
    return subprocess.run(
        ["bash", str(SCRIPT), "--check-secrets", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestCheckSecrets:
    def test_both_keys_present_passes(self, tmp_path: Path):
        result = _check(tmp_path, "TURNSTILE_SITE_KEY=0xSITE\nTURNSTILE_SECRET=0xSECRET\n")
        assert result.returncode == 0, result.stderr

    def test_secret_only_fails_naming_the_site_key(self, tmp_path: Path):
        # THE review shape: the old grep guard passed this, deploying
        # with captcha silently off.
        result = _check(tmp_path, "TURNSTILE_SECRET=0xSECRET\n")
        assert result.returncode == 3, result.stderr
        assert "TURNSTILE_SITE_KEY" in result.stderr

    def test_site_key_only_fails_naming_the_secret(self, tmp_path: Path):
        result = _check(tmp_path, "TURNSTILE_SITE_KEY=0xSITE\n")
        assert result.returncode == 3, result.stderr
        assert "TURNSTILE_SECRET" in result.stderr

    def test_quoted_empty_value_fails(self, tmp_path: Path):
        # The old `grep | cut` guard counted `TURNSTILE_SECRET=""` as a
        # value; the wrapper-parity parser treats it as disabled.
        result = _check(tmp_path, 'TURNSTILE_SITE_KEY=0xSITE\nTURNSTILE_SECRET=""\n')
        assert result.returncode == 3, result.stderr
        assert "TURNSTILE_SECRET" in result.stderr

    def test_export_prefix_accepted(self, tmp_path: Path):
        # The old `^TURNSTILE_SECRET=` grep REJECTED export-style lines
        # that the wrapper merge (and python-dotenv, and systemd) accept.
        result = _check(
            tmp_path,
            "export TURNSTILE_SITE_KEY=0xSITE\nexport TURNSTILE_SECRET=0xSECRET\n",
        )
        assert result.returncode == 0, result.stderr

    def test_missing_file_fails(self, tmp_path: Path):
        result = _check(tmp_path, content=None)
        assert result.returncode == 3, result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="wrapper test hooks are non-root-only")
    def test_parity_with_wrapper_merge(self, tmp_path: Path):
        # The load-bearing invariant: any secrets.env the guard accepts
        # must merge BOTH keys via the wrapper (same parser semantics) —
        # if these ever diverge, a guard-approved file could still deploy
        # captcha-off.
        content = "# comment\nexport TURNSTILE_SITE_KEY=\"0xSITE\"\nTURNSTILE_SECRET='0xSECRET'\n"
        assert _check(tmp_path, content).returncode == 0

        dest = tmp_path / "runtime-config.json"
        env = os.environ.copy()
        env["KAYAK_INSTALL_DEST"] = str(dest)
        env["KAYAK_INSTALL_SECRETS"] = str(tmp_path / "secrets.env")
        result = subprocess.run(
            ["bash", str(WRAPPER)],
            input="{}",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(dest.read_text())
        assert data["turnstile_site_key"] == "0xSITE"
        assert data["turnstile_secret"] == "0xSECRET"
