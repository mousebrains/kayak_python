#!/usr/bin/env bash
# Dump levels_todo from production MySQL via ssh wkcc, then import into local SQLite.
#
# Usage:
#   ./scripts/dump_and_import.sh                  # full import (metadata + observations)
#   ./scripts/dump_and_import.sh --skip-timeseries # metadata only (fast, ~30s)
#
# Prerequisites:
#   - ssh wkcc works without a password prompt (key-based auth)
#   - ../.venv has kayak installed (relative to repo root)

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/../.venv"
DB="$REPO/../DB/kayak.db"
DUMP_DIR=/tmp
DUMP_FILE="$DUMP_DIR/levels_todo.sql"

MYSQL_HOST=mysql.wkcc.dreamhosters.com
MYSQL_USER=levels
MYSQL_PASS=Deschutes
MYSQL_DB=levels_todo

IMPORT_ARGS=("$@")

echo "=== Step 1: Dumping $MYSQL_DB from production ==="
ssh wkcc "mysqldump --single-transaction --skip-lock-tables \
    -h '$MYSQL_HOST' -u '$MYSQL_USER' -p'$MYSQL_PASS' \
    '$MYSQL_DB'" > "$DUMP_FILE"

dump_size=$(du -h "$DUMP_FILE" | cut -f1)
echo "  Dump saved to $DUMP_FILE ($dump_size)"

echo ""
echo "=== Step 2: Importing into $DB ==="
"$VENV/bin/python" "$REPO/scripts/import_from_dump.py" \
    --dump "$DUMP_FILE" \
    --db "$DB" \
    "${IMPORT_ARGS[@]}"

echo ""
echo "=== Step 3: Syncing sources.yaml URLs and linking sources ==="
"$VENV/bin/levels" init-db
"$VENV/bin/python" "$REPO/scripts/link_sources.py" --db "$DB"

echo ""
echo "=== Step 4: Running pipeline ==="
"$VENV/bin/levels" pipeline

echo ""
echo "=== Done ==="
ls -lh "$DB"
rm -f "$DUMP_FILE"
echo "Cleaned up $DUMP_FILE"
