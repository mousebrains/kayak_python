"""Release-context snapshot — replaces syncit's ``release/*`` outputs.

Captures the same three signals syncit harvested into
``release/deploy-paths.txt``, ``release/git.log``, and
``release/db-health.txt``: the build's last-deploy time (index.html
mtime), the git commits in the analysis window, and a few sanity-
check counts from kayak.db.

Each helper is a thin wrapper — they could be inlined but live in
this module so the release-postmortem code reads as a sequence of
"snapshot → analyze" steps rather than mixed-concern subprocess
plumbing.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import subprocess
from pathlib import Path

_DEFAULT_INDEX_HTML = Path("/home/pat/public_html/index.html")
_DEFAULT_KAYAK_DB = Path("/home/pat/DB/kayak.db")
_DEFAULT_REPO_ROOT = Path("/home/pat/kayak")


def infer_release_time(
    index_html: Path = _DEFAULT_INDEX_HTML,
    tz: dt.tzinfo | None = None,
) -> dt.datetime | None:
    """mtime of ``index.html`` — the build writes it last, so its mtime
    tracks "last successful build deployed."

    Returns None if the file is missing (fresh install / wrong path).
    Caller is expected to fall back to a CLI flag in that case.
    """
    try:
        ts = index_html.stat().st_mtime
    except FileNotFoundError:
        return None
    tz = tz or dt.UTC
    return dt.datetime.fromtimestamp(ts, tz=tz)


def git_log_since(
    since: dt.datetime,
    repo_root: Path = _DEFAULT_REPO_ROOT,
) -> list[str]:
    """Short pretty-format git log entries since ``since``.

    Empty list if git fails (not a repo, git missing). The release
    postmortem only uses this to enumerate "what changed during the
    analysis window" — a failure here doesn't block the rest of the
    report.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "log",
            "--since",
            since.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "--date=iso",
            "--pretty=format:%h %ad %an %s",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def db_health_snapshot(
    db_path: Path = _DEFAULT_KAYAK_DB,
) -> dict[str, str]:
    """Sanity-check counts from kayak.db.

    Returns ``{}`` if the DB is missing or unreadable. Same query
    shape as syncit's ``release/db-health.txt`` so the operator's
    eyes are pattern-matched on familiar numbers.
    """
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        out: dict[str, str] = {}
        for metric, sql in (
            ("observation_count", "SELECT count(*) FROM observation"),
            ("latest_observation_count", "SELECT count(*) FROM latest_observation"),
            (
                "latest_gauge_observation_count",
                "SELECT count(*) FROM latest_gauge_observation",
            ),
            ("schema_head", "SELECT max(version) FROM schema_migrations"),
        ):
            try:
                row = conn.execute(sql).fetchone()
                out[metric] = str(row[0]) if row and row[0] is not None else ""
            except sqlite3.Error:
                out[metric] = "?"
        return out
    finally:
        conn.close()


def deploy_paths_listing(
    docroot_pattern: str = "/home/pat/public_html*",
) -> list[str]:
    """``ls -la /home/pat/public_html*`` equivalent — for release-postmortem
    "what's at /home/pat/public_html(/.staging/...) right now?" footer.

    Returns ``[]`` if the path doesn't exist.
    """
    out: list[str] = []
    for path in sorted(Path("/").glob(docroot_pattern.lstrip("/"))):
        try:
            st = path.stat()
        except OSError:
            continue
        kind = "d" if path.is_dir() else ("l" if path.is_symlink() else "f")
        size = st.st_size if kind == "f" else 0
        mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.UTC).isoformat()
        link = ""
        if path.is_symlink():
            link = " -> " + os.readlink(path)
        out.append(f"{kind} {size:>10} {mtime} {path}{link}")
    return out
