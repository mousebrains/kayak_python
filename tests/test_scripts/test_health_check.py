"""Regression tests for ``scripts/health-check.sh`` per-source freshness.

The 2026-06-03 project review (gpt-5.5) found the script only checked
the single global ``MAX(observed_at)``: as long as *any* source kept
writing, a dead feed — or an active source that never produced data at
all — was invisible to the alerting path, while ``docs/slo.md`` SLO F
promised per-source freshness. These tests run the real script via
``bash`` against a file-backed SQLite DB built from the ORM schema and
pin the per-source check:

- an active, gauge-linked, fetch-backed source with NO observations
  fails the check even when the global timestamp is fresh;
- one silent for more than ``STALE_SOURCE_DAYS`` (default 14) fails;
- OGC-fetched USGS sources (gauge-linked, no fetch_url row) fail once
  fed-then-silent, but never-fed ones are exempt (speculative metadata
  additions awaiting upstream coverage — operator decision 2026-06-03);
- inactive, gauge-unlinked, and calc-backed sources are ignored
  (orphan-check's job / derived data);
- the pre-existing global staleness check still fires.

Host-dependent checks (disk %, swap, systemd timer) are neutralized via
the script's env knobs plus a PATH-shimmed ``systemctl``, so the tests
exercise only the DB-driven logic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from kayak.db.models import (
    Base,
    DataType,
    FetchUrl,
    Gauge,
    GaugeSource,
    LatestObservation,
    Source,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "health-check.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None or shutil.which("bash") is None,
    reason="health-check.sh needs the sqlite3 and bash CLIs",
)


def _utcnow() -> datetime:
    """Naive UTC now, matching how the pipeline stores observed_at."""
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _seed_source(
    session: Session,
    name: str,
    *,
    active: bool = True,
    linked: bool = True,
    ogc: bool = False,
    agency: str = "NWS",
    latest: datetime | None,
) -> Source:
    """Create a source; optionally link a gauge and an observation.

    ``ogc=True`` models an OGC-fetched USGS source: ``agency='USGS'``
    with NO fetch_url row (``levels fetch-usgs-ogc`` selects those via
    the gauge link alone).
    """
    if ogc:
        fetch_url_id = None
        agency = "USGS"
    else:
        fetch_url = FetchUrl(url=f"https://example.com/{name}", parser="nwps", is_active=active)
        session.add(fetch_url)
        session.flush()
        fetch_url_id = fetch_url.id
    source = Source(name=name, agency=agency, fetch_url_id=fetch_url_id)
    session.add(source)
    session.flush()
    if linked:
        gauge = Gauge(name=f"gauge_{name}")
        session.add(gauge)
        session.flush()
        session.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))
    if latest is not None:
        session.add(
            LatestObservation(
                source_id=source.id,
                data_type=DataType.flow,
                observed_at=latest,
                value=100.0,
            )
        )
    session.flush()
    return source


@pytest.fixture
def db_session(tmp_path: Path) -> tuple[Path, Session]:
    """A file-backed SQLite DB (the script shells out to sqlite3) + session."""
    db_path = tmp_path / "kayak.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return db_path, Session(engine)


def _run_health_check(
    db_path: Path,
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # Shim systemctl: on a systemd host (CI) `kayak-pipeline.timer` is
    # never active, which would trip the script's timer check before the
    # per-source logic under test.
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "systemctl"
    shim.write_text("#!/bin/sh\necho active\n")
    shim.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "SQLITE_PATH": str(db_path),
            "PATH": f"{shim_dir}:{env['PATH']}",
            # Neutralize host-dependent checks: CI runners commonly sit
            # above the 70% disk WARN threshold on /home.
            "DISK_WARN_PCT": "101",
            "DISK_FAIL_PCT": "101",
            "SWAP_USED_PCT_WARN": "101",
            "MEM_FREE_MB_WARN": "0",
            # Per-test state file so the per-source alert rate-limit doesn't leak
            # across tests (or write to the real $HOME). A fresh empty state means
            # a first run alerts on a silent source, as the legacy tests expect.
            "HEALTHCHECK_STATE_FILE": str(tmp_path / "hc-source-alerts.tsv"),
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, check=False
    )


class TestPerSourceFreshness:
    def test_all_fresh_sources_pass(self, db_session: tuple[Path, Session], tmp_path: Path):
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "b", latest=_utcnow() - timedelta(hours=2))
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout

    def test_dead_source_fails_even_when_global_is_fresh(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        # THE regression: source `a` keeps the global MAX fresh while
        # `dead` has been silent for 20 days.
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "dead" in result.stdout
        assert "14 days" in result.stdout

    def test_source_with_no_observations_fails(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "neverfed", latest=None)
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "neverfed" in result.stdout
        assert "NEVER" in result.stdout

    def test_inactive_and_unlinked_sources_are_ignored(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        # Deactivated fetch_url: stale is fine (feed intentionally off).
        _seed_source(session, "retired", active=False, latest=_utcnow() - timedelta(days=30))
        # No gauge_source link: orphan-check's territory, not ours.
        _seed_source(session, "orphan", linked=False, latest=None)
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_stale_source_days_is_overridable(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "slow", latest=_utcnow() - timedelta(days=3))
        session.commit()
        # 3 days silent passes the 14-day default…
        assert _run_health_check(db_path, tmp_path).returncode == 0
        # …but fails a tightened 2-day window.
        result = _run_health_check(db_path, tmp_path, extra_env={"STALE_SOURCE_DAYS": "2"})
        assert result.returncode == 1, result.stdout + result.stderr
        assert "slow" in result.stdout

    def test_ogc_source_gone_silent_fails(self, db_session: tuple[Path, Session], tmp_path: Path):
        # OGC-fetched USGS source (no fetch_url row) that HAD data and
        # went dark > STALE_SOURCE_DAYS — same dead-feed semantics as a
        # fetch-backed source.
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "ogc_dead", ogc=True, latest=_utcnow() - timedelta(days=20))
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "ogc_dead" in result.stdout

    def test_ogc_source_never_fed_is_exempt(self, db_session: tuple[Path, Session], tmp_path: Path):
        # Never-fed OGC sources are speculative metadata additions
        # awaiting upstream coverage — exempt by operator decision
        # (2026-06-03), unlike never-fed fetch-backed sources.
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "ogc_pending", ogc=True, latest=None)
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_ogc_source_fresh_passes(self, db_session: tuple[Path, Session], tmp_path: Path):
        db_path, session = db_session
        _seed_source(session, "ogc_live", ogc=True, latest=_utcnow() - timedelta(hours=1))
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_calc_source_without_fetch_url_is_ignored(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        # Non-USGS source with no fetch_url row models a calc-backed
        # source — out of scope (its inputs are what get checked).
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "calc", ogc=True, latest=_utcnow() - timedelta(days=30))
        # Rewrite the agency: same no-fetch_url shape, but not USGS.
        session.execute(
            Source.__table__.update().where(Source.name == "calc").values(agency="calc")
        )
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_usgs_with_inactive_fetch_url_still_checked_via_ogc_arm(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        # fetch-usgs-ogc selects ALL gauge-linked USGS sources regardless
        # of fetch_url, so deactivating a USGS source's fetch_url doesn't
        # stop its data flow — a fed-then-silent one is still a dead feed.
        # (The `fu.is_active IS NOT 1` arm; a plain fetch_url_id IS NULL
        # scope would leave this shape unmonitored.)
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(
            session,
            "usgs_inactive_dead",
            active=False,
            agency="USGS",
            latest=_utcnow() - timedelta(days=20),
        )
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "usgs_inactive_dead" in result.stdout

    def test_non_numeric_stale_source_days_is_config_error(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        session.commit()
        result = _run_health_check(db_path, tmp_path, extra_env={"STALE_SOURCE_DAYS": "2 weeks"})
        assert result.returncode == 2, result.stdout + result.stderr
        assert "STALE_SOURCE_DAYS" in result.stdout


class TestGlobalFreshness:
    def test_global_stale_still_fails(self, db_session: tuple[Path, Session], tmp_path: Path):
        # Pre-existing behavior: newest observation older than 3 h.
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(hours=5))
        session.commit()
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Latest observation" in result.stdout


class TestSourceAlertThrottle:
    """Per-source alert rate-limit + mute (the check still runs frequently)."""

    def test_dead_source_alerts_once_then_suppressed_within_window(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        session.commit()

        first = _run_health_check(db_path, tmp_path)
        assert first.returncode == 1, first.stdout + first.stderr
        assert "dead" in first.stdout

        # Same tmp_path → same state file → the second run is inside the window.
        second = _run_health_check(db_path, tmp_path)
        assert second.returncode == 0, second.stdout + second.stderr
        assert "OK" in second.stdout
        assert "within their" in second.stdout
        assert "dead" not in second.stdout

    def test_dead_source_realerts_after_window(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        dead = _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        session.commit()

        assert _run_health_check(db_path, tmp_path).returncode == 1
        # Age this source's last-alert past the 7-day window → it's due again.
        state = tmp_path / "hc-source-alerts.tsv"
        old = int((_utcnow() - timedelta(days=8)).timestamp())
        state.write_text(f"{dead.id}\t{old}\n")

        again = _run_health_check(db_path, tmp_path)
        assert again.returncode == 1, again.stdout + again.stderr
        assert "dead" in again.stdout

    def test_muted_source_not_alerted_but_listed(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        dead = _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        session.commit()

        result = _run_health_check(
            db_path, tmp_path, extra_env={"HEALTHCHECK_MUTE_SOURCES": str(dead.id)}
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "dead" not in result.stdout
        assert f"muted sources: {dead.id}" in result.stdout

    def test_mute_rejects_non_numeric_injection(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        # A non-numeric mute value is sanitized to digits/commas, so it can't
        # inject SQL; here it sanitizes to empty → the dead source still alerts.
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))  # keep global fresh
        _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        session.commit()
        result = _run_health_check(
            db_path, tmp_path, extra_env={"HEALTHCHECK_MUTE_SOURCES": "x); DROP TABLE source;--"}
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "dead" in result.stdout

    def test_recovered_source_drops_from_state_and_realerts(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))  # keep global fresh
        dead = _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        session.commit()
        assert _run_health_check(db_path, tmp_path).returncode == 1  # dies → alert

        obs = session.query(LatestObservation).filter_by(source_id=dead.id).one()
        obs.observed_at = _utcnow() - timedelta(minutes=10)  # recovers
        session.commit()
        assert _run_health_check(db_path, tmp_path).returncode == 0  # not silent → OK

        obs.observed_at = _utcnow() - timedelta(days=20)  # dies again
        session.commit()
        again = _run_health_check(db_path, tmp_path)
        # Fresh alert — the pre-recovery state entry was pruned, not reused.
        assert again.returncode == 1, again.stdout + again.stderr
        assert "dead" in again.stdout

    def test_state_persist_failure_is_not_green(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        # P2: a silent source that WOULD be suppressed by a stale entry must not
        # exit green when the state can't be persisted (unwritable dir) — otherwise
        # a recovered-then-dead source could be hidden. The same condition fails
        # every run, so a stale entry can't coexist with a green run.
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))  # global fresh
        dead = _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        session.commit()
        ro = tmp_path / "ro"
        ro.mkdir()
        state = ro / "hc.tsv"
        # A recent alert → 'dead' would be suppressed (within the 7d window)...
        state.write_text(f"{dead.id}\t{int(datetime.now(UTC).timestamp())}\n")
        ro.chmod(0o500)  # ...but the state dir is unwritable → prune/write fails.
        try:
            result = _run_health_check(
                db_path, tmp_path, extra_env={"HEALTHCHECK_STATE_FILE": str(state)}
            )
        finally:
            ro.chmod(0o700)  # restore so pytest can clean tmp_path up
        assert result.returncode == 2, result.stdout + result.stderr
        assert "could not be persisted" in result.stdout

    def test_corrupt_state_epoch_falls_back_to_alerting(
        self, db_session: tuple[Path, Session], tmp_path: Path
    ):
        # A hand-clobbered non-numeric/leading-zero epoch must not crash the check
        # (set -e) — it's sanitized + base-10 forced, treated as ancient → alerts.
        db_path, session = db_session
        _seed_source(session, "a", latest=_utcnow() - timedelta(minutes=30))
        dead = _seed_source(session, "dead", latest=_utcnow() - timedelta(days=20))
        session.commit()
        state = tmp_path / "hc-source-alerts.tsv"  # the path the harness uses
        state.write_text(f"{dead.id}\t08garbage\n")
        result = _run_health_check(db_path, tmp_path)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "dead" in result.stdout
