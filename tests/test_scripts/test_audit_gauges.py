"""Unit tests for scripts/audit_gauges.py noise-reduction filters.

The script lives outside src/ so we import it via importlib path. These tests
build a tiny in-memory SQLite DB with just the columns the audit queries touch
and exercise the two false-positive sources we tightened:

  * ``check_data_status`` STARTED FEEDS — a newly-added gauge (no history
    before the window) must NOT be reported as a "started feed"; only an
    established feed that went quiet then resumed should be.
  * ``find_candidates_near_reaches`` — already-gauged reaches, distant gauges,
    and wrong-river name mismatches must all be filtered out by default.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

_AUDIT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_gauges.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("audit_gauges", _AUDIT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db() -> sqlite3.Connection:
    """A minimal schema covering only the columns the audit queries read."""
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE gauge (id INTEGER PRIMARY KEY, name TEXT, usgs_id TEXT);
        CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE gauge_source (gauge_id INTEGER, source_id INTEGER);
        CREATE TABLE observation (source_id INTEGER, data_type TEXT, observed_at TEXT);
        CREATE TABLE latest_observation (source_id INTEGER, data_type TEXT, observed_at TEXT);
        CREATE TABLE reach (
            id INTEGER PRIMARY KEY, display_name TEXT, name TEXT, river TEXT,
            gauge_id INTEGER, no_show INTEGER DEFAULT 0,
            latitude_start REAL, longitude_start REAL,
            latitude_end REAL, longitude_end REAL
        );
        """
    )
    return db


def _add_gauge(db, gid, name, usgs_id=None, source_id=None):
    source_id = source_id if source_id is not None else gid
    db.execute("INSERT INTO gauge (id, name, usgs_id) VALUES (?,?,?)", (gid, name, usgs_id))
    db.execute("INSERT INTO source (id, name) VALUES (?,?)", (source_id, name))
    db.execute("INSERT INTO gauge_source (gauge_id, source_id) VALUES (?,?)", (gid, source_id))
    return source_id


def _obs(db, source_id, when, data_type="flow"):
    db.execute(
        "INSERT INTO observation (source_id, data_type, observed_at) VALUES (?,?,?)",
        (source_id, data_type, when.strftime("%Y-%m-%d %H:%M:%S")),
    )


# --------------------------------------------------------------------------
# STARTED FEEDS: new gauge vs. genuinely restarted feed
# --------------------------------------------------------------------------


def test_started_excludes_brand_new_gauge():
    """A gauge whose entire history begins inside the window is NOT 'started'."""
    audit = _load_audit()
    db = _make_db()
    now = datetime.now(UTC)
    sid = _add_gauge(db, 1, "06090500", "06090500")
    # Dense history, but all of it is within the last few days (newly wired).
    for d in range(1, 6):
        _obs(db, sid, now - timedelta(days=d))
    db.commit()

    _stopped, started, _stale = audit.check_data_status(db, days=7)
    assert [s[1] for s in started] == [], "newly-added gauge must not be a started feed"


def test_started_includes_restarted_feed():
    """A gauge with pre-window history, a quiet gap, then recent obs IS 'started'."""
    audit = _load_audit()
    db = _make_db()
    now = datetime.now(UTC)
    sid = _add_gauge(db, 2, "RESTART1", "12345678")
    # Old history (older than the 2*N=14d window), then a >=7d quiet gap,
    # then fresh data inside the last 7 days.
    _obs(db, sid, now - timedelta(days=20))
    _obs(db, sid, now - timedelta(days=16))
    _obs(db, sid, now - timedelta(days=2))
    _obs(db, sid, now - timedelta(days=1))
    db.commit()

    _stopped, started, _stale = audit.check_data_status(db, days=7)
    assert [s[1] for s in started] == ["RESTART1"], "restarted feed must be reported"


# --------------------------------------------------------------------------
# NEW CANDIDATES: gauged / distance / river-name filters
# --------------------------------------------------------------------------


def _reach(db, rid, display_name, river, gauge_id, lat, lon):
    # A zero-length reach so midpoint == (lat, lon); keeps distance math simple.
    db.execute(
        "INSERT INTO reach (id, display_name, name, river, gauge_id, no_show, "
        "latitude_start, longitude_start, latitude_end, longitude_end) "
        "VALUES (?,?,?,?,?,0,?,?,?,?)",
        (rid, display_name, display_name, river, gauge_id, lat, lon, lat, lon),
    )


def test_candidate_skips_already_gauged_reach_by_default():
    audit = _load_audit()
    db = _make_db()
    # Reach already has a linked gauge (gauge_id=99), same river name as candidate.
    _reach(db, 1, "Rock Creek", "Rock Creek", 99, 44.0, -122.0)
    db.commit()
    # Candidate sits right on it and shares the "rock" token.
    new = [("14317600", "ROCK CREEK NEAR GLIDE, OR", 44.0, -122.0, None, None)]

    default = audit.find_candidates_near_reaches(new, db, kind="USGS")
    assert default == [], "already-gauged reach suppressed by default"

    opted_in = audit.find_candidates_near_reaches(new, db, kind="USGS", include_gauged=True)
    assert [c[1] for c in opted_in] == ["14317600"], "--include-gauged surfaces it"


def test_candidate_requires_shared_river_name_beyond_half_mile():
    audit = _load_audit()
    db = _make_db()
    # Ungauged reach; candidate ~1 mi away on a DIFFERENT stream.
    _reach(db, 1, "Canal Creek", "Canal Creek", None, 44.0, -122.0)
    db.commit()
    # ~1 mi north (≈0.0145 deg lat) — beyond the 0.5 mi name-override.
    wrong = [("X1", "QUARTZVILLE CREEK BLW GALENA CREEK", 44.0145, -122.0, None, None)]
    assert audit.find_candidates_near_reaches(wrong, db, kind="USGS") == [], (
        "wrong-river candidate must be dropped"
    )

    # Same spot, but a station name that shares the 'canal' identity token.
    right = [("X2", "CANAL CREEK NEAR ALSEA", 44.0145, -122.0, None, None)]
    assert [c[1] for c in audit.find_candidates_near_reaches(right, db, kind="USGS")] == ["X2"]


def test_candidate_distance_threshold():
    audit = _load_audit()
    db = _make_db()
    _reach(db, 1, "Rock Creek", "Rock Creek", None, 44.0, -122.0)
    db.commit()
    # ~5 mi away (≈0.0725 deg lat), shares the name token but too far for 3 mi cap.
    far = [("FAR", "ROCK CREEK ABV SOMEWHERE", 44.0725, -122.0, None, None)]
    assert audit.find_candidates_near_reaches(far, db, kind="USGS") == []
    assert [c[1] for c in audit.find_candidates_near_reaches(far, db, max_dist_miles=10)] == ["FAR"]


def test_candidate_name_override_within_half_mile():
    """A gauge essentially on the run surfaces even with a non-matching name."""
    audit = _load_audit()
    db = _make_db()
    _reach(db, 1, "Steamboat Creek", "Steamboat Creek", None, 44.0, -122.0)
    db.commit()
    # ~0.2 mi away, name shares no token with the reach.
    on_top = [("ONTOP", "USGS 14999999 SOME LANDMARK", 44.003, -122.0, None, None)]
    assert [c[1] for c in audit.find_candidates_near_reaches(on_top, db, kind="USGS")] == ["ONTOP"]
