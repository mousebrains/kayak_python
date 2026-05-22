"""Smoke tests for ``levels status`` (kayak.cli.status).

Mocks subprocess + /proc/meminfo so the test doesn't depend on the
host's actual state. Verifies that:
- The renderer produces a non-empty HTML file at the requested path.
- Disk + swap thresholds drive WARN/FAIL classes correctly.
- The output is written atomically via os.replace (the tempfile
  has the target's parent dir).
- The bot/human classifier output flows through into rendered HTML.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import pytest

from kayak.cli import status


def _fake_cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def _mock_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, ...], str]:
    """Patch kayak.cli.status._run to return canned output by argv prefix."""

    canned: dict[tuple[str, ...], str] = {
        # df -P /home
        ("df",): (
            "Filesystem     1024-blocks      Used  Available Capacity Mounted on\n"
            "/dev/sda1         38000000  21500000   14500000      60% /\n"
        ),
        # systemctl list-units --type=service kayak-*
        ("systemctl", "list-units"): (
            "kayak-pipeline.service      loaded active running OK\n"
            "kayak-healthcheck.service   loaded inactive dead   OK\n"
            "kayak-notify-failure@kayak-pipeline.service.service  loaded inactive dead OK\n"
        ),
        # systemctl show <unit> -p ...
        ("systemctl", "show", "kayak-pipeline.service"): (
            "ActiveState=active\n"
            "Result=success\n"
            "ExecMainStatus=0\n"
            "ExecMainStartTimestamp=Thu 2026-05-21 15:16:24 PDT\n"
            "ExecMainExitTimestamp=Thu 2026-05-21 15:18:22 PDT\n"
        ),
        ("systemctl", "show", "kayak-healthcheck.service"): (
            "ActiveState=inactive\n"
            "Result=success\n"
            "ExecMainStatus=0\n"
            "ExecMainStartTimestamp=Thu 2026-05-21 15:45:26 PDT\n"
            "ExecMainExitTimestamp=Thu 2026-05-21 15:45:27 PDT\n"
        ),
        ("systemctl", "show", "kayak-backup-offsite.service"): (
            "ExecMainStartTimestamp=Thu 2026-05-21 03:30:00 PDT\nResult=success\n"
        ),
        # journalctl -u <unit> --since=-24h -p err -n 3
        ("journalctl",): "-- No entries --\n",
        # openssl s_client (returns a fake cert blob)
        (
            "openssl",
            "s_client",
        ): "-----BEGIN CERTIFICATE-----\nMIIFAKE\n-----END CERTIFICATE-----\n",
        ("openssl", "x509"): "notAfter=Aug 17 18:01:00 2026 GMT\n",
    }

    def _fake_run(cmd: list[str], **_kw) -> subprocess.CompletedProcess[str]:
        # longest matching prefix wins so "systemctl show <unit>" beats "systemctl"
        best_key: tuple[str, ...] = ()
        for key in canned:
            if len(key) > len(best_key) and tuple(cmd[: len(key)]) == key:
                best_key = key
        return _fake_cp(stdout=canned.get(best_key, ""))

    monkeypatch.setattr(status, "_run", _fake_run)
    return canned


@pytest.fixture
def _mock_meminfo(monkeypatch: pytest.MonkeyPatch):
    """Patch _read_meminfo to return a known-good system at 30% swap, 100 MB MemAvailable."""

    def _fake(*_a, **_kw) -> dict[str, int]:
        # MemAvailable in kB; 102400 kB = 100 MB which is below MEM_FREE_MB_WARN (400).
        return {
            "MemTotal": 1_966_080,  # ~1.9 GB
            "MemAvailable": 102_400,  # 100 MB
            "SwapTotal": 4_194_304,  # 4 GB
            "SwapFree": 2_936_012,  # ~28 MB used? No — let me recompute. 4194304-2936012=1258292 kB used = 30%
        }

    monkeypatch.setattr(status, "_read_meminfo", _fake)


def _fake_humans(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass log parsing — kayak.analytics.humans needs real /var/log/nginx access."""
    monkeypatch.setattr(
        status.humans,
        "run_chunked",
        lambda **kw: (
            "# Human / bot traffic\n\n| bucket | humans | bots |\n|---|---|---|\n| 09:00 | 100 | 20 |\n"
        ),
    )
    monkeypatch.setattr(
        status.humans,
        "run_humans",
        lambda **kw: (
            "# Distinct human visitors\n\n- **5** distinct human-looking IPs\n- 120 total human hits\n"
        ),
    )
    monkeypatch.setattr(
        status.humans,
        "run_paths",
        lambda **kw: (
            "# Hits by URL path\n\n| path | human | bot | other | total |\n"
            "|---|---|---|---|---|\n| `/` | 50 | 10 | 5 | 65 |\n"
        ),
    )
    monkeypatch.setattr(
        status.humans,
        "run_countries",
        lambda **kw: (
            "# Hits by country\n\n| country | human hits | human IPs | bot | other | total |\n"
            "|---|---|---|---|---|---|\n| United States (US) | 80 | 5 | 10 | 0 | 90 |\n"
        ),
    )
    monkeypatch.setattr(
        status.humans,
        "run_asns",
        lambda **kw: (
            "# Hits by autonomous system\n\n"
            "| organization | human hits | human IPs | bot | other | total |\n"
            "|---|---|---|---|---|---|\n| Comcast Cable (AS7922) | 80 | 5 | 0 | 0 | 80 |\n"
        ),
    )
    monkeypatch.setattr(
        status.humans,
        "run_subdivisions",
        lambda **kw: (
            "# CA / US states & provinces\n\n"
            "| subdivision | human hits | human IPs | bot | other | total |\n"
            "|---|---|---|---|---|---|\n| Oregon (US) | 25 | 3 | 0 | 0 | 25 |\n"
        ),
    )


def _ns(output: Path) -> argparse.Namespace:
    return argparse.Namespace(
        output=str(output),
        hours=24,
        bucket_hours=4,
        tz="America/Los_Angeles",
        log_glob="/var/log/nginx/*access.log*",
    )


def test_renders_non_empty_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mock_subprocess,
    _mock_meminfo,
) -> None:
    _fake_humans(monkeypatch)
    out = tmp_path / "status.html"
    rc = status.run(_ns(out))
    assert rc == 0
    assert out.exists()
    body = out.read_text()
    assert "<!doctype html>" in body
    assert "<h1>Operator status —" in body
    assert "<h2>Traffic (24h)</h2>" in body
    assert "<h2>Disk &amp; memory</h2>" in body
    assert "<h2>systemd jobs</h2>" in body
    assert "<h2>Backups &amp; cert</h2>" in body
    # Cross-link to /_internal/ and /status.json.
    assert "/_internal/" in body
    assert "/status.json" in body
    # noindex meta is present.
    assert 'name="robots"' in body and "noindex" in body


def test_disk_below_warn_marks_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mock_subprocess,
    _mock_meminfo,
) -> None:
    _fake_humans(monkeypatch)
    out = tmp_path / "status.html"
    status.run(_ns(out))
    body = out.read_text()
    # Mock df reports 60% used — below WARN of 70%.
    assert '<tr class="ok"><th>Disk /home</th>' in body


def test_disk_above_fail_marks_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mock_meminfo,
) -> None:
    _fake_humans(monkeypatch)

    canned_full = {
        ("df",): (
            "Filesystem     1024-blocks      Used  Available Capacity Mounted on\n"
            "/dev/sda1         38000000  33500000    4500000      88% /\n"
        ),
        ("systemctl", "list-units"): "",
        (
            "systemctl",
            "show",
            "kayak-backup-offsite.service",
        ): "ExecMainStartTimestamp=\nResult=success\n",
        ("journalctl",): "",
        ("openssl", "s_client"): "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n",
        ("openssl", "x509"): "notAfter=Aug 17 18:01:00 2026 GMT\n",
    }

    def _run(cmd, **_kw):
        best: tuple[str, ...] = ()
        for k in canned_full:
            if len(k) > len(best) and tuple(cmd[: len(k)]) == k:
                best = k
        return _fake_cp(stdout=canned_full.get(best, ""))

    monkeypatch.setattr(status, "_run", _run)
    out = tmp_path / "status.html"
    status.run(_ns(out))
    body = out.read_text()
    assert '<tr class="fail"><th>Disk /home</th>' in body


def test_swap_conjunction_marks_warn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mock_subprocess,
) -> None:
    _fake_humans(monkeypatch)
    # >10% swap used (1.25 GB / 4 GB ≈ 30%) AND <400 MB MemAvailable (100 MB) → warn.
    monkeypatch.setattr(
        status,
        "_read_meminfo",
        lambda: {
            "MemTotal": 1_966_080,
            "MemAvailable": 102_400,
            "SwapTotal": 4_194_304,
            "SwapFree": 2_936_012,
        },
    )
    out = tmp_path / "status.html"
    status.run(_ns(out))
    body = out.read_text()
    assert '<tr class="warn"><th>Swap</th>' in body


def test_swap_under_thresholds_marks_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mock_subprocess,
) -> None:
    _fake_humans(monkeypatch)
    # 1% swap used, 1.3 GB MemAvailable → ok.
    monkeypatch.setattr(
        status,
        "_read_meminfo",
        lambda: {
            "MemTotal": 1_966_080,
            "MemAvailable": 1_300_000,
            "SwapTotal": 4_194_304,
            "SwapFree": 4_152_360,
        },
    )
    out = tmp_path / "status.html"
    status.run(_ns(out))
    body = out.read_text()
    assert '<tr class="ok"><th>Swap</th>' in body


def test_humans_markdown_flows_into_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mock_subprocess,
    _mock_meminfo,
) -> None:
    monkeypatch.setattr(
        status.humans,
        "run_chunked",
        lambda **kw: (
            "# Human / bot traffic\n\n| bucket | humans | bots |\n|---|---|---|\n| 09:00 | 100 | 20 |\n"
        ),
    )
    monkeypatch.setattr(
        status.humans,
        "run_humans",
        lambda **kw: "- **42** distinct human-looking IPs\n",
    )
    monkeypatch.setattr(status.humans, "run_paths", lambda **kw: "")
    monkeypatch.setattr(status.humans, "run_countries", lambda **kw: "")
    monkeypatch.setattr(status.humans, "run_subdivisions", lambda **kw: "")
    monkeypatch.setattr(status.humans, "run_asns", lambda **kw: "")
    out = tmp_path / "status.html"
    status.run(_ns(out))
    body = out.read_text()
    # Pipe table rendered to <table>.
    assert "<th>bucket</th>" in body
    assert "<td>100</td>" in body
    # **bold** in run_humans rendered to <strong>.
    assert "<strong>42</strong>" in body


def test_atomic_write_no_leftover_tmpfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _mock_subprocess,
    _mock_meminfo,
) -> None:
    _fake_humans(monkeypatch)
    out = tmp_path / "status.html"
    status.run(_ns(out))
    # Only the final file should remain in the dir (no .tmp leftovers).
    files = list(tmp_path.iterdir())
    assert files == [out], f"expected one file, found {files}"


def test_atomic_write_cleans_tmpfile_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "status.html"
    # Force an error mid-write by patching os.replace.
    monkeypatch.setattr(
        os, "replace", lambda *_: (_ for _ in ()).throw(OSError("simulated failure"))
    )
    with pytest.raises(OSError, match="simulated failure"):
        status._atomic_write(out, "fake content")
    # Tempfile should be cleaned up.
    assert list(tmp_path.iterdir()) == []


def test_parse_systemd_timestamp_handles_naive() -> None:
    parsed = status._parse_systemd_timestamp("Thu 2026-05-21 15:16:24 PDT")
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == (2026, 5, 21)
    assert (parsed.hour, parsed.minute, parsed.second) == (15, 16, 24)


def test_parse_systemd_timestamp_handles_empty() -> None:
    assert status._parse_systemd_timestamp("") is None
    assert status._parse_systemd_timestamp("0") is None
    assert status._parse_systemd_timestamp("n/a") is None
