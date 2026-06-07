#!/usr/bin/env python3
"""Regenerate ``src/kayak/data/db/migrations/manifest.csv`` (S9a).

The manifest is the authoritative **active** migration set —
``version,filename,sha256`` in version order. ``kayak.cli.migrate.discover_migrations``
reads it (instead of globbing) and verifies each file's sha256, so the manifest,
not a directory glob, defines which migrations are active and detects a migration
edited after being recorded. Adding (or rarely editing) a migration requires
rerunning this script; a test (``tests/test_cli/test_migrate.py``) fails CI if the
committed manifest drifts from the files.

    python3 scripts/gen_migration_manifest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kayak.cli.migrate import MIGRATIONS_DIR, regenerate_manifest


def main() -> int:
    n = regenerate_manifest()
    print(f"wrote {MIGRATIONS_DIR / 'manifest.csv'} ({n} migrations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
