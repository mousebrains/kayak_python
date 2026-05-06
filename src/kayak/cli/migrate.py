"""Schema migration runner.

Usage:
    levels migrate                    # apply all pending migrations
    levels migrate --status           # list applied vs pending
    levels migrate --stamp 0002       # mark a version as applied w/o running

Migrations live in ``data/db/migrations/NNNN_description.sql`` and are
tracked in a ``schema_migrations`` table. The runner applies each pending
file in a transaction.

Fresh databases (``levels init-db``) stamp the current migration set
after ``Base.metadata.create_all()`` so the SQL files don't re-run.
Existing pre-migration-system databases need a one-time
``levels migrate --stamp <current_version>`` bootstrap.
"""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from kayak.config import DATA_DIR
from kayak.db.engine import get_engine

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = DATA_DIR / "db" / "migrations"
_VERSION_RE = re.compile(r"^(\d{4})_")


@dataclass(frozen=True)
class Migration:
    """One .sql file on disk."""

    version: str  # zero-padded string, e.g. "0002"
    name: str  # full filename stem, e.g. "0002_no_flow_range"
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text()


def _ensure_tracking_table() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL"
                ")"
            )
        )


def discover_migrations(migrations_dir: Path | None = None) -> list[Migration]:
    """Return every migration file in version order.

    Uses ``MIGRATIONS_DIR`` by default — resolved at call time so tests can
    monkeypatch the module constant.
    """
    root = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR
    if not root.is_dir():
        return []
    out: list[Migration] = []
    for path in sorted(root.glob("*.sql")):
        m = _VERSION_RE.match(path.name)
        if not m:
            logger.warning("Skipping non-versioned migration file: %s", path.name)
            continue
        out.append(Migration(version=m.group(1), name=path.stem, path=path))
    return out


def applied_versions() -> set[str]:
    """Return the set of versions recorded in schema_migrations."""
    _ensure_tracking_table()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).all()
    return {r[0] for r in rows}


def stamp(version: str) -> None:
    """Record ``version`` as applied without running its SQL."""
    _ensure_tracking_table()
    engine = get_engine()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (:v, :a)"),
            {"v": version, "a": now},
        )


def stamp_all_known() -> int:
    """Stamp every discovered migration (used by ``init-db`` on fresh DBs)."""
    count = 0
    for m in discover_migrations():
        if m.version not in applied_versions():
            stamp(m.version)
            count += 1
    return count


def apply_pending() -> list[str]:
    """Run every migration not yet recorded. Return the versions applied."""
    _ensure_tracking_table()
    engine = get_engine()
    applied = applied_versions()
    migrations = discover_migrations()
    pending = [m for m in migrations if m.version not in applied]
    if not pending:
        return []

    ran: list[str] = []
    for m in pending:
        logger.info("Applying migration %s", m.name)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        sql = m.sql
        statements = _split_statements(sql)
        if _wants_no_transaction(sql):
            # Migration manages its own transaction(s) — needed for ops like
            # PRAGMA foreign_keys=OFF that SQLite silently ignores inside a
            # surrounding transaction. Run each statement in autocommit, then
            # record the version in a separate transaction.
            raw = engine.raw_connection()
            try:
                cur = raw.cursor()
                # Drop SQLAlchemy's BEGIN; we want true autocommit so PRAGMAs
                # take effect and the migration's own BEGIN/COMMIT pair runs.
                raw.isolation_level = None  # type: ignore[attr-defined]
                for stmt in statements:
                    cur.execute(stmt)
                cur.close()
                raw.commit()
            finally:
                raw.close()
            with engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :a)"),
                    {"v": m.version, "a": now},
                )
        else:
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.execute(
                    text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :a)"),
                    {"v": m.version, "a": now},
                )
        ran.append(m.version)
    return ran


def _wants_no_transaction(sql: str) -> bool:
    """True if the migration opts out of the runner's wrapping transaction.

    Marker is the literal token ``@no_transaction`` anywhere in the leading
    SQL comment block. Used by table-rebuild migrations that need
    ``PRAGMA foreign_keys=OFF`` to take effect (PRAGMA is silently ignored
    mid-transaction in SQLite).
    """
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("--"):
            return False
        if "@no_transaction" in stripped:
            return True
    return False


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script on ``;`` boundaries, skipping comments and blanks.

    Strips ``-- ...`` line comments first so semicolons inside prose don't
    confuse the splitter, then separates on the remaining ``;`` tokens.
    """
    # Drop everything from `--` to end of line on every line.
    no_line_comments = "\n".join(re.sub(r"--.*$", "", ln) for ln in sql.splitlines())
    out: list[str] = []
    for raw in no_line_comments.split(";"):
        stmt = raw.strip()
        if stmt:
            out.append(stmt)
    return out


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'migrate' subcommand."""
    parser = subparsers.add_parser("migrate", help="Apply pending schema migrations")
    parser.set_defaults(func=migrate)
    parser.add_argument(
        "--status", action="store_true", help="Show applied / pending migrations and exit"
    )
    parser.add_argument(
        "--stamp",
        metavar="VERSION",
        action="append",
        default=[],
        help="Mark VERSION as applied without running its SQL (repeatable)",
    )
    parser.add_argument(
        "--stamp-all",
        action="store_true",
        help="Stamp every discovered migration as applied (one-time bootstrap for pre-migration DBs)",
    )


def migrate(args: argparse.Namespace) -> None:
    """Entry point for ``levels migrate``."""
    if args.stamp_all:
        stamped = stamp_all_known()
        print(f"Stamped {stamped} migration(s) as applied.")
        return

    if args.stamp:
        for v in args.stamp:
            stamp(v)
            print(f"Stamped {v} as applied.")
        return

    if args.status:
        applied = applied_versions()
        migrations = discover_migrations()
        print(f"{'version':<10}{'status':<10}{'name'}")
        for m in migrations:
            state = "applied" if m.version in applied else "pending"
            print(f"{m.version:<10}{state:<10}{m.name}")
        unknown = applied - {m.version for m in migrations}
        for v in sorted(unknown):
            print(f"{v:<10}{'applied':<10}(no file)")
        return

    ran = apply_pending()
    if ran:
        print("Applied migrations: " + ", ".join(ran))
    else:
        print("No pending migrations.")
