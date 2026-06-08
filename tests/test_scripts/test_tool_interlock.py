"""The maintenance scripts refuse the configured production DB (SA-3 / AC #6).

``refresh_reach_elevations.py`` and ``seed_gauge_display.py`` author dataset-owned
columns; an ``--apply`` run against the configured ``DATABASE_URL`` is refused before
any work. Driven as subprocesses (the real entry points) since they aren't a package.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = ["refresh_reach_elevations.py", "seed_gauge_display.py"]


def _run(
    script: str, *args: str, db_url: str | None = None, kayak_db: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("KAYAK_DB", None)  # so an omitted --db resolves to the configured DATABASE_URL
    if db_url is not None:
        env["DATABASE_URL"] = db_url
    if kayak_db is not None:
        env["KAYAK_DB"] = kayak_db
    return subprocess.run(
        [sys.executable, str(_REPO / "scripts" / script), *args],
        capture_output=True,
        text=True,
        cwd=_REPO,
        env=env,
        timeout=120,
    )


@pytest.mark.parametrize("script", _SCRIPTS)
def test_apply_refuses_configured_db(script: str, tmp_path: Path) -> None:
    """--apply with no --db (resolves to the configured DB) is refused before any
    work, exiting 2 with a clear message."""
    db = tmp_path / "live.db"
    r = _run(script, "--apply", db_url=f"sqlite:///{db}")
    assert r.returncode == 2, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "refusing to mutate the configured database" in r.stderr


@pytest.mark.parametrize("script", _SCRIPTS)
def test_apply_refuses_even_with_kayak_db_set(script: str, tmp_path: Path) -> None:
    """The legacy KAYAK_DB env can't silently become an --apply target: an omitted
    --db resolves to the configured DB (refused), even when KAYAK_DB points at a
    different real DB. Regression for the SA-3 review fail-open."""
    r = _run(
        script,
        "--apply",
        db_url=f"sqlite:///{tmp_path / 'configured.db'}",
        kayak_db=str(tmp_path / "kayakdb.db"),
    )
    assert r.returncode == 2, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "refusing to mutate the configured database" in r.stderr


@pytest.mark.parametrize("script", _SCRIPTS)
def test_allow_production_flag_parses(script: str) -> None:
    """The new --allow-production flag is wired into argparse without breaking it."""
    r = _run(script, "--help")
    assert r.returncode == 0, r.stderr
    assert "--allow-production" in r.stdout
