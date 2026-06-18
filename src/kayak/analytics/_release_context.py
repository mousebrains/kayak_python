"""Release-context snapshot — replaces syncit's ``release/*`` outputs.

Captures the same three signals syncit harvested into
``release/deploy-paths.txt``, ``release/git.log``, and
``release/db-health.txt``: the last-deploy time (the
``/opt/kayak/current`` release-pointer mtime), the git commits in the
analysis window, and a few sanity-check counts from kayak.db.

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

from kayak.host import HostConfig, get_host_config

# The release root is owned by typed host config (``HostConfig.release_root``,
# default ``/opt/kayak``), so the activation pointer and the layout listing
# derive from it rather than re-hardcoding the root — a host that relocates the
# release root in ``host.yaml`` is then followed automatically, like every other
# cutover consumer. (The DB / repo defaults below have no host-config field and
# did not move in the cutover, so they stay literal.)
_DEFAULT_KAYAK_DB = Path("/home/pat/DB/kayak.db")
_DEFAULT_REPO_ROOT = Path("/home/pat/kayak")


def _release_root() -> str:
    """``release_root`` from host config, degrading to the engine default
    (``/opt/kayak``) if ``host.yaml`` is unreadable or malformed.

    ``analyze-logs`` is a read-only operator diagnostic — often run *because* a
    host is unhealthy — so a broken ``host.yaml`` must not turn it into a
    traceback. Falling back keeps inference degrading to ``None`` and the footer
    to ``[]`` the way a missing pointer does, and keeps an explicit ``--release``
    a reliable escape hatch (``run_postmortem`` still loads the footer, so a
    raising config load would crash the report even when ``--release`` is given).
    ``get_host_config`` is fail-closed (raises ``ValueError`` on a malformed
    file, ``OSError`` on an unreadable one); both mean "use the default root".

    NOTE: this is a deliberate, narrowly-scoped divergence from
    ``load_host_config``'s fail-closed contract ("a malformed host config must
    not let a consumer silently fall back to another host's defaults"). It is
    safe *only* because this consumer is a read-only diagnostic and the fallback
    is benign — it degrades to ``None`` → ``--release`` (never a wrong write) —
    and a malformed ``host.yaml`` is already a loud failure in every
    write/serving consumer an operator would notice first. Do NOT "fix" this
    back into a raise; that reintroduces the traceback this guard removes.
    """
    try:
        return get_host_config().release_root
    except (OSError, ValueError):
        return HostConfig().release_root


def current_release_link() -> Path:
    """The ``current`` release pointer, ``{release_root}/current``.

    ``kayak-deploy.sh`` performs the cutover by atomically relinking this
    symlink (and restores it on rollback), so the symlink's OWN mtime is set at
    the relink = the instant the release went live. This replaced the old
    ``/home/pat/public_html/index.html`` mtime, which the 2026-06 cutover
    orphaned — that file still exists but froze at the cutover mtime, so
    ``infer_release_time`` silently returned a stale timestamp forever.
    """
    return Path(_release_root()) / "current"


def infer_release_time(
    current_link: Path | None = None,
    tz: dt.tzinfo | None = None,
) -> dt.datetime | None:
    """mtime of the ``current`` release pointer (``{release_root}/current``) —
    the deploy's atomic relink of this symlink IS the activation cutover, so
    its mtime is the instant the release went live.

    The pointer MUST be a symlink: only the deploy's ``ln -s`` gives it an mtime
    that means "activation instant". A non-symlink at ``current`` (a stale
    directory or regular file from a botched layout) is rejected with ``None``
    rather than trusted — otherwise its own mtime would silently seed a bogus
    window, the exact present-but-wrong failure this signal replaced. ``lstat``
    then reads the *symlink's* own mtime (not the target dir's), so a
    momentarily-broken ``current`` still yields a time rather than vanishing.

    Returns None if the pointer is absent, not a symlink, or unreadable (fresh
    install / pre-cutover host / dev box). Caller is expected to fall back to a
    CLI flag in that case.
    """
    link = current_release_link() if current_link is None else current_link
    try:
        if not link.is_symlink():
            return None
        ts = link.lstat().st_mtime
    except OSError:
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
    layout_pattern: str | None = None,
) -> list[str]:
    """``ls -la {release_root}/*`` equivalent — for release-postmortem
    "which release is live right now?" footer: shows ``current`` (with the
    release it points at), ``releases``, and ``maintenance`` if present.

    The 2026-06 cutover moved the served tree into the paired-release layout,
    so this lists the release pointer rather than the retired
    ``/home/pat/public_html``. ``lstat`` keeps the ``current`` symlink showing
    its own activation mtime and surviving a broken target. Hidden entries are
    skipped (``ls`` without ``-a``) — ``Path.glob('*')`` matches dotfiles, but
    the deploy scratch (``.staging``, transient ``.swap.NNN``) is noise for a
    "which release is live" footer.

    Returns ``[]`` if nothing matches the pattern.
    """
    pattern = f"{_release_root()}/*" if layout_pattern is None else layout_pattern
    out: list[str] = []
    for path in sorted(Path("/").glob(pattern.lstrip("/"))):
        if path.name.startswith("."):
            continue
        try:
            st = path.lstat()
        except OSError:
            continue
        kind = "l" if path.is_symlink() else ("d" if path.is_dir() else "f")
        size = st.st_size if kind == "f" else 0
        mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.UTC).isoformat()
        link = ""
        if path.is_symlink():
            link = " -> " + os.readlink(path)
        out.append(f"{kind} {size:>10} {mtime} {path}{link}")
    return out
