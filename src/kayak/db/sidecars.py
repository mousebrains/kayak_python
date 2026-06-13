"""Reach geometry/gradient sidecar apply — the packaged deploy surface.

``reach.geom`` and ``reach.gradient_profile`` are dataset content but are
**excluded** from ``reach.csv`` (large, machine-generated), living instead in
``reaches.json`` / ``reaches-gradient.json`` at the dataset root. They are
deliberately NOT written by ``sync-metadata``; this module is their only
sanctioned live-DB writer (used by ``levels import-metadata``, the
``scripts/import_metadata.py`` wrapper, and ``kayak-deploy`` activation —
PR #190 review: the paired-release deployer must apply them too, or a
sidecar-only dataset release silently serves stale geometry).

Functions return ``(applied, unmatched)`` row counts and raise
``ValueError`` on a malformed snapshot; presentation/exit-code policy stays
with the callers.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

GEOM_JSON = "reaches.json"
GRADIENT_JSON = "reaches-gradient.json"


def _load_pairs(path: Path) -> list[tuple[str, int]]:
    with path.open(encoding="utf-8") as f:
        try:
            data = json.load(f)
            return [(value, int(rid)) for rid, value in data.items()]
        except (json.JSONDecodeError, ValueError, AttributeError) as exc:
            raise ValueError(f"{path} is malformed ({exc})") from exc


def apply_geom(conn: sqlite3.Connection, dataset_dir: Path) -> tuple[int, int]:
    """Apply ``reach.geom`` from ``reaches.json``; absent file is a no-op.

    Returns ``(rows_applied, snapshot_entries_unmatched)`` — unmatched > 0
    almost always means "ran before ``levels sync-metadata``" or the wrong DB.
    """
    path = dataset_dir / GEOM_JSON
    if not path.exists():
        return (0, 0)
    pairs = _load_pairs(path)
    cur = conn.executemany("UPDATE reach SET geom = ? WHERE id = ?", pairs)
    return (cur.rowcount, len(pairs) - cur.rowcount)


def apply_gradient(conn: sqlite3.Connection, dataset_dir: Path) -> tuple[int, int]:
    """Apply ``reach.gradient_profile`` from ``reaches-gradient.json``."""
    path = dataset_dir / GRADIENT_JSON
    if not path.exists():
        return (0, 0)
    pairs = _load_pairs(path)
    cur = conn.executemany("UPDATE reach SET gradient_profile = ? WHERE id = ?", pairs)
    return (cur.rowcount, len(pairs) - cur.rowcount)
