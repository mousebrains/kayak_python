"""Regression tests for ``deploy/kayak-install-runtime-config.sh``.

The gpt-5.5 take-2 review (2026-06-03) found the review-3 R1.5 wrapper
split silently broke Turnstile in production: ``levels emit-config
--dry-run`` renders as unprivileged ``pat``, which cannot read
``/etc/kayak/secrets.env`` (0600 root:www-data), so the piped JSON
arrived without ``turnstile_site_key`` / ``turnstile_secret`` and PHP's
``turnstile_enabled()`` false-pathed to "captcha off" — confirmed live
(login page rendered with no Turnstile widget). The wrapper now merges
secrets.env into the JSON before installing.

These tests run the real script as the current (non-root) user via the
``KAYAK_INSTALL_DEST`` / ``KAYAK_INSTALL_SECRETS`` test hooks — which
the script honors only when euid != 0, so the root/sudoers behavior
keeps its fixed paths.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "kayak-install-runtime-config.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.geteuid() == 0,
    reason="needs bash and a non-root euid (test hooks are non-root-only)",
)


def _run(
    tmp_path: Path,
    stdin: str,
    secrets: str | None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    dest = tmp_path / "runtime-config.json"
    env = os.environ.copy()
    env["KAYAK_INSTALL_DEST"] = str(dest)
    if secrets is None:
        env["KAYAK_INSTALL_SECRETS"] = str(tmp_path / "no-such-secrets.env")
    else:
        secrets_path = tmp_path / "secrets.env"
        secrets_path.write_text(secrets)
        env["KAYAK_INSTALL_SECRETS"] = str(secrets_path)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result, dest


class TestSecretsMerge:
    def test_fills_turnstile_keys_absent_from_rendered_json(self, tmp_path: Path):
        # THE production shape: pat-rendered JSON lacks both keys
        # (SecretStr fields excluded as None), secrets.env has them.
        result, dest = _run(
            tmp_path,
            json.dumps({"database_path": "/x.db"}),
            "TURNSTILE_SITE_KEY=0xSITE\nTURNSTILE_SECRET=0xSECRET\n",
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(dest.read_text())
        assert data["turnstile_site_key"] == "0xSITE"
        assert data["turnstile_secret"] == "0xSECRET"
        assert data["database_path"] == "/x.db"
        assert "installed" in result.stdout

    def test_rendered_value_wins_over_secrets(self, tmp_path: Path):
        # Parity with config.py's load_dotenv(..., override=False):
        # the operator's env (already in the rendered JSON) wins.
        result, dest = _run(
            tmp_path,
            json.dumps({"turnstile_site_key": "from-operator-env"}),
            "TURNSTILE_SITE_KEY=from-secrets\nTURNSTILE_SECRET=s\n",
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(dest.read_text())
        assert data["turnstile_site_key"] == "from-operator-env"
        assert data["turnstile_secret"] == "s"

    def test_empty_rendered_value_is_filled(self, tmp_path: Path):
        result, dest = _run(
            tmp_path,
            json.dumps({"turnstile_secret": ""}),
            "TURNSTILE_SECRET=real\n",
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(dest.read_text())["turnstile_secret"] == "real"

    def test_parses_comments_blanks_and_quotes(self, tmp_path: Path):
        result, dest = _run(
            tmp_path,
            "{}",
            '# comment\n\nTURNSTILE_SECRET="quoted value"\nNOT_A_PAIR_LINE\n',
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(dest.read_text())
        assert data["turnstile_secret"] == "quoted value"
        assert "not_a_pair_line" not in data

    def test_empty_secret_value_is_skipped(self, tmp_path: Path):
        # secrets.env.example convention: empty TURNSTILE_SECRET means
        # "Turnstile disabled" — must not inject an empty key.
        result, dest = _run(tmp_path, "{}", "TURNSTILE_SECRET=\n")
        assert result.returncode == 0, result.stderr
        assert "turnstile_secret" not in json.loads(dest.read_text())

    def test_missing_secrets_file_is_noop(self, tmp_path: Path):
        result, dest = _run(tmp_path, json.dumps({"a": 1}), secrets=None)
        assert result.returncode == 0, result.stderr
        assert json.loads(dest.read_text()) == {"a": 1}

    def test_export_prefix_is_stripped(self, tmp_path: Path):
        # `export KEY=VALUE` parses identically in python-dotenv and
        # systemd EnvironmentFile; the wrapper must match, not mint a
        # bogus "export turnstile_secret" key (adversarial review,
        # take-2 round).
        result, dest = _run(tmp_path, "{}", "export TURNSTILE_SECRET=via-export\n")
        assert result.returncode == 0, result.stderr
        data = json.loads(dest.read_text())
        assert data["turnstile_secret"] == "via-export"
        assert "export turnstile_secret" not in data


class TestInstallGuards:
    def test_rejects_non_json(self, tmp_path: Path):
        result, dest = _run(tmp_path, "this is not json", "TURNSTILE_SECRET=s\n")
        assert result.returncode != 0
        assert not dest.exists()
        # trap cleaned the staging file
        assert not list(tmp_path.glob(".runtime-config.*"))

    def test_rejects_non_object_json(self, tmp_path: Path):
        result, dest = _run(tmp_path, "[1, 2, 3]", "TURNSTILE_SECRET=s\n")
        assert result.returncode != 0
        assert not dest.exists()

    def test_replaces_existing_dest_and_sets_mode(self, tmp_path: Path):
        dest = tmp_path / "runtime-config.json"
        dest.write_text(json.dumps({"old": True}))
        result, dest = _run(tmp_path, json.dumps({"new": True}), "")
        assert result.returncode == 0, result.stderr
        data = json.loads(dest.read_text())
        assert data == {"new": True}
        assert (dest.stat().st_mode & 0o777) == 0o640
