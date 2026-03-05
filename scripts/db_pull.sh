#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="pat@levels.mousebrains.com"
REMOTE_DB="/home/pat/DB/kayak.db"
LOCAL_DB="../DB/kayak.db"

echo "Checkpointing WAL on remote..."
ssh "$REMOTE_HOST" "sqlite3 '$REMOTE_DB' 'PRAGMA wal_checkpoint(TRUNCATE);'"

echo "Pulling database from $REMOTE_HOST:$REMOTE_DB ..."
rsync -avz "$REMOTE_HOST:$REMOTE_DB" "$LOCAL_DB"

echo "Removing stale local WAL/SHM files..."
rm -f "${LOCAL_DB}-wal" "${LOCAL_DB}-shm"

echo "Done. Local database: $LOCAL_DB"
sqlite3 "$LOCAL_DB" "SELECT 'Reaches: ' || count(*) FROM reach;"
