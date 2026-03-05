#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="pat@levels.mousebrains.com"
REMOTE_DB="/home/pat/DB/kayak.db"
LOCAL_DB="../DB/kayak.db"
FORCE=false

while getopts "f" opt; do
    case $opt in
        f) FORCE=true ;;
        *) echo "Usage: $0 [-f]" >&2; exit 1 ;;
    esac
done

if [ ! -f "$LOCAL_DB" ]; then
    echo "Error: $LOCAL_DB not found" >&2
    exit 1
fi

if [ "$FORCE" != true ]; then
    read -rp "Overwrite remote database at $REMOTE_HOST:$REMOTE_DB? [y/N] " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

echo "Checkpointing local WAL..."
sqlite3 "$LOCAL_DB" "PRAGMA wal_checkpoint(TRUNCATE);"

echo "Pushing database to $REMOTE_HOST:$REMOTE_DB ..."
rsync -avz "$LOCAL_DB" "$REMOTE_HOST:$REMOTE_DB"

echo "Removing stale remote WAL/SHM files..."
ssh "$REMOTE_HOST" "rm -f '${REMOTE_DB}-wal' '${REMOTE_DB}-shm'"

echo "Done."
