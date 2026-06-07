"""Schema migration runner.

Usage:
    levels migrate                    # apply all pending migrations
    levels migrate --status           # list applied vs pending
    levels migrate --check            # exit non-zero if any migration is pending
    levels migrate --stamp 0002       # mark a version as applied w/o running

Migrations live in ``src/kayak/data/db/migrations/NNNN_description.sql`` and are
tracked in a ``schema_migrations`` table. The runner applies each pending
file in a transaction.

Fresh databases (``levels init-db``) stamp the current migration set
after ``Base.metadata.create_all()`` so the SQL files don't re-run.
Existing pre-migration-system databases need a one-time
``levels migrate --stamp <current_version>`` bootstrap.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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
# The manifest (version,filename,sha256) is the authoritative ACTIVE migration set
# (S9a): discovery reads it instead of globbing, so a frozen/legacy file outside the
# manifest is never picked up, and an edited migration is caught by its sha256.
# Regenerate with scripts/gen_migration_manifest.py.
MANIFEST_NAME = "manifest.csv"
_VERSION_RE = re.compile(r"^(\d{4})_")


@dataclass(frozen=True)
class Migration:
    """One .sql file on disk."""

    version: str  # zero-padded string, e.g. "0002"
    name: str  # full filename stem, e.g. "0002_no_flow_range"
    path: Path
    digest: str  # sha256 recorded in the manifest (verified against the file)

    @property
    def sql(self) -> str:
        return self.path.read_text()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_tracking_table() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL, digest TEXT"
                ")"
            )
        )
        # Pre-S9a DBs (the live host) have the 2-column table; add `digest` if it's
        # missing. Idempotent — runs on every migrate/init-db; legacy rows keep a
        # NULL digest (tolerated; only new rows record the applied sha256).
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(schema_migrations)")).all()}
        if "digest" not in cols:
            conn.execute(text("ALTER TABLE schema_migrations ADD COLUMN digest TEXT"))


def discover_migrations(migrations_dir: Path | None = None) -> list[Migration]:
    """Return the active migrations, in version order.

    Reads ``manifest.csv`` (the authoritative active set, S9a) rather than globbing:
    each listed file must exist and its sha256 must match the manifest (a mismatch
    means a migration was edited after being recorded — regenerate with
    ``scripts/gen_migration_manifest.py``), and a ``*.sql`` in the dir that the
    manifest omits is an error (added without updating the manifest). ``MIGRATIONS_DIR``
    is resolved at call time so tests can monkeypatch / pass a dir.
    """
    root = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR
    if not root.is_dir():
        return []
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise ValueError(
            f"migration manifest not found: {manifest}. Run "
            "`python3 scripts/gen_migration_manifest.py` to (re)generate it."
        )
    out: list[Migration] = []
    seen: dict[str, str] = {}  # version -> filename, for the dup-version guard
    listed: set[str] = set()
    with manifest.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            version = (row.get("version") or "").strip()
            filename = (row.get("filename") or "").strip()
            sha = (row.get("sha256") or "").strip()
            if not (version and filename and sha):
                raise ValueError(f"{MANIFEST_NAME}: malformed row {row!r}")
            if version in seen:
                raise ValueError(
                    f"{MANIFEST_NAME}: duplicate version {version!r} ({seen[version]!r} and "
                    f"{filename!r}). The NNNN_ prefix is the schema_migrations PRIMARY KEY; "
                    "renumber one of them."
                )
            seen[version] = filename
            listed.add(filename)
            path = root / filename
            if not path.is_file():
                raise ValueError(f"{MANIFEST_NAME}: lists {filename!r} but the file is missing.")
            actual = _file_sha256(path)
            if actual != sha:
                raise ValueError(
                    f"{MANIFEST_NAME}: sha256 mismatch for {filename!r} (manifest "
                    f"{sha[:12]}…, file {actual[:12]}…) — a migration was edited after being "
                    "recorded. Regenerate with scripts/gen_migration_manifest.py."
                )
            out.append(Migration(version=version, name=path.stem, path=path, digest=sha))
    orphans = sorted(p.name for p in root.glob("*.sql") if p.name not in listed)
    if orphans:
        raise ValueError(
            f"{MANIFEST_NAME}: *.sql file(s) not in the manifest: {orphans}. Run "
            "scripts/gen_migration_manifest.py."
        )
    out.sort(key=lambda mig: mig.version)
    return out


def regenerate_manifest(migrations_dir: Path = MIGRATIONS_DIR) -> int:
    """(Re)write ``manifest.csv`` for *migrations_dir* from its ``*.sql`` files —
    ``version,filename,sha256`` in version order. The single source of truth for
    the manifest, shared by ``scripts/gen_migration_manifest.py`` and the tests so
    generation can't drift from what :func:`discover_migrations` verifies. Returns
    the migration count."""
    rows: list[tuple[str, str, str]] = []
    seen: dict[str, str] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        m = _VERSION_RE.match(path.name)
        if not m:
            continue
        version = m.group(1)
        if version in seen:
            raise ValueError(
                f"duplicate migration version {version!r}: {seen[version]!r} and {path.name!r}"
            )
        seen[version] = path.name
        rows.append((version, path.name, _file_sha256(path)))
    rows.sort(key=lambda r: r[0])
    with (migrations_dir / MANIFEST_NAME).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["version", "filename", "sha256"])
        w.writerows(rows)
    return len(rows)


def applied_versions() -> set[str]:
    """Return the set of versions recorded in schema_migrations."""
    _ensure_tracking_table()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version FROM schema_migrations")).all()
    return {r[0] for r in rows}


def pending_migrations(applied: set[str] | None = None) -> list[Migration]:
    """Discovered migrations not yet recorded in schema_migrations, in order.

    Single-sources the "what's pending" computation shared by apply_pending()
    and ``migrate --check``. Pass ``applied`` to reuse an already-read set.
    """
    if applied is None:
        applied = applied_versions()
    return [m for m in discover_migrations() if m.version not in applied]


def stamp(version: str, digest: str | None = None) -> None:
    """Record ``version`` as applied without running its SQL.

    ``digest`` is the migration's manifest sha256 when known (recorded for new
    rows); ``None`` for a bare legacy-version bootstrap (``--stamp`` of a version
    with no active file)."""
    _ensure_tracking_table()
    engine = get_engine()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, digest) "
                "VALUES (:v, :a, :d)"
            ),
            {"v": version, "a": now, "d": digest},
        )


def stamp_all_known() -> int:
    """Stamp every discovered migration (used by ``init-db`` on fresh DBs)."""
    count = 0
    already = applied_versions()  # read once; the loop stamps distinct versions
    for m in discover_migrations():
        if m.version not in already:
            stamp(m.version, m.digest)
            count += 1
    return count


def apply_pending() -> list[str]:
    """Run every migration not yet recorded. Return the versions applied."""
    _ensure_tracking_table()
    engine = get_engine()
    pending = pending_migrations()
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
                    text(
                        "INSERT INTO schema_migrations (version, applied_at, digest) "
                        "VALUES (:v, :a, :d)"
                    ),
                    {"v": m.version, "a": now, "d": m.digest},
                )
        else:
            with engine.begin() as conn:
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.execute(
                    text(
                        "INSERT INTO schema_migrations (version, applied_at, digest) "
                        "VALUES (:v, :a, :d)"
                    ),
                    {"v": m.version, "a": now, "d": m.digest},
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


def _reject_literal_with_delimiter(sql: str) -> None:
    """Raise if a single-quoted literal embeds ``;`` or ``--``.

    Those are exactly the tokens ``_split_statements`` keys on (statement
    separator, line-comment start); its line-wise strip + ``split(';')`` would
    truncate a statement that buries one inside a string. No committed migration
    does, so this turns a future foot-gun into a clear discovery-time error
    rather than a silently mangled statement (review-4 R5.5). SQLite's in-literal
    ``''`` quote escape is respected.
    """
    i = 0
    n = len(sql)
    in_string = False
    while i < n:
        ch = sql[i]
        if in_string:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":  # '' escape -> stays in string
                    i += 2
                    continue
                in_string = False
            elif ch == ";" or (ch == "-" and i + 1 < n and sql[i + 1] == "-"):
                raise ValueError(
                    "Migration SQL embeds ';' or '--' inside a string literal; the "
                    "statement splitter would truncate it. Rewrite to avoid the "
                    "embedded token (e.g. char(59) for ';')."
                )
        elif ch == "'":
            in_string = True
        elif ch == "-" and i + 1 < n and sql[i + 1] == "-":
            nl = sql.find("\n", i)  # skip a line comment so its quotes don't count
            if nl == -1:
                break
            i = nl + 1
            continue
        i += 1


def _split_statements(sql: str) -> list[str]:
    """Split a SQL script on ``;`` boundaries, skipping comments and blanks.

    Strips ``-- ...`` line comments first so semicolons inside prose don't
    confuse the splitter, then separates on the remaining ``;`` tokens. The
    splitter doesn't parse string literals, so ``_reject_literal_with_delimiter``
    first rejects any migration that buries ``;``/``--`` inside one.
    """
    _reject_literal_with_delimiter(sql)
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
        "--check",
        action="store_true",
        help="Exit non-zero if any migration is pending (deploy/snapshot guard); applies nothing",
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
        known = {m.version: m for m in discover_migrations()}
        for v in args.stamp:
            stamp(v, known[v].digest if v in known else None)
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

    if args.check:
        pending = pending_migrations()
        if pending:
            versions = ", ".join(m.version for m in pending)
            # Non-zero exit lets deploy/snapshot guards refuse to run against a
            # half-migrated DB (scripts/snapshot_metadata.sh): the nightly git
            # pull can bring migration files live without `levels migrate`.
            raise SystemExit(
                "migrate --check: pending migration(s) not applied to this DB: "
                + versions
                + " — run `levels migrate` before snapshotting/deploying."
            )
        print("migrate --check: all migrations applied.")
        return

    ran = apply_pending()
    if ran:
        print("Applied migrations: " + ", ".join(ran))
    else:
        print("No pending migrations.")
