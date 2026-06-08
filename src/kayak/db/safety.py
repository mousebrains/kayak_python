"""Write-safety guard for dataset-owned metadata tables.

dataset-separation SA / acceptance criterion #6: engine runtime and maintenance
commands must not mutate dataset-owned metadata in the live (configured) database
directly — those changes go through a reviewed CSV diff + ``levels sync-metadata``.

Maintenance tools that *author* dataset-owned columns (``levels assign-huc``,
``scripts/refresh_reach_elevations.py``, ``scripts/seed_gauge_display.py``) keep a
direct-DB write mode, but must run against an explicitly named scratch/dev copy and
**refuse the configured production database** unless overridden. The dev flow stays
"run on a scratch/dev DB → ``export_metadata`` → reviewed CSV edit → PR to
``kayak_data`` → ``sync-metadata`` to prod".
"""

from __future__ import annotations

from pathlib import Path

from kayak.config import DATABASE_URL


class ProductionWriteRefused(RuntimeError):
    """A maintenance tool tried to mutate the configured (live) database directly."""


def resolve_db_path(database_url: str | None) -> Path:
    """The on-disk SQLite path from a ``sqlite://`` URL (or a bare path).

    ``None`` / empty means the configured :data:`kayak.config.DATABASE_URL`.
    """
    url = database_url or DATABASE_URL
    if "://" in url:
        from sqlalchemy.engine import make_url

        db = make_url(url).database
        if not db:
            raise ValueError(f"no database path in URL: {url!r}")
        return Path(db)
    return Path(url)


def as_sqlite_url(target: str) -> str:
    """A ``sqlite://`` URL for a bare filesystem path; an existing URL passes through.

    ``get_session`` / ``create_engine`` need a URL, but the maintenance tools accept a
    plain ``--db`` path for convenience.
    """
    if "://" in target:
        return target
    return f"sqlite:///{Path(target).resolve()}"


def _same_file(a: Path, b: Path) -> bool:
    """Whether two paths point at the same on-disk file (symlinks resolved)."""
    try:
        return a.resolve() == b.resolve()
    except OSError:  # a path component is unreadable — fall back to a lexical compare
        return a == b


def refuse_configured_db(target: str | Path | None, *, allow_production: bool = False) -> None:
    """Raise :class:`ProductionWriteRefused` if ``target`` is the configured DB.

    ``target`` of ``None`` / empty resolves to the configured database (the ambient
    default), so a tool that silently writes the configured DB is refused too.
    ``allow_production=True`` is the explicit operator override.
    """
    if allow_production:
        return
    configured = resolve_db_path(None)
    target_path = resolve_db_path(str(target)) if target else configured
    if _same_file(target_path, configured):
        raise ProductionWriteRefused(
            f"refusing to mutate the configured database ({configured}) directly — "
            "dataset-owned metadata changes go via a reviewed CSV diff + "
            "`levels sync-metadata`. Point --db at a scratch/dev copy, or pass "
            "--allow-production to override."
        )
