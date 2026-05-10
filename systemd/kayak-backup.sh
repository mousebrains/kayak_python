#!/usr/bin/env bash
# Weekly SQLite backup with retention policy.
#
# Filenames are backup-YYYYMMDDTHHMMSSZ.db.gz (UTC, second-resolution).
# A new timestamp on every invocation makes the script idempotent under
# same-day re-runs — the gzip step never collides with an existing file.
#
# Keeps at most 4 backups: the newest, plus those at positions
# 1, 3, and 5 in the sorted list (roughly 1, 3, and 5 weeks old).
#
# Uses sqlite3 .backup for a consistent snapshot of a live database.
# Backups are gzip-compressed (level 4, ~82% size reduction).

set -euo pipefail

DB="${SQLITE_PATH:-/home/pat/DB/kayak.db}"
BACKUP_DIR="/home/pat/kayak/backups"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$BACKUP_DIR/backup-$STAMP.db"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB" ]]; then
    echo "Error: database not found at $DB" >&2
    exit 1
fi

# Clean up any uncompressed leftovers from a prior failed run *first*, so
# `set -e` doesn't skip this step if today's gzip fails for any reason.
rm -f "$BACKUP_DIR"/backup-[0-9]*T[0-9]*Z.db

# Create backup (safe for live DB — sqlite3 .backup holds a read lock)
sqlite3 "$DB" ".backup $DEST"
echo "Backed up to $DEST ($(du -h "$DEST" | cut -f1))"

# Compress with gzip level 4 (~82% reduction, fast)
gzip -4 "$DEST"
echo "Compressed to $DEST.gz ($(du -h "$DEST.gz" | cut -f1))"

# Retention: keep backups at positions 0, 1, 3, 5 (newest first)
# This gives coverage at 0, ~1, ~3, ~5 weeks back
keep_positions=(0 1 3 5)

mapfile -t backups < <(ls -1r "$BACKUP_DIR"/backup-[0-9]*T[0-9]*Z.db.gz 2>/dev/null)

for i in "${!backups[@]}"; do
    keep=false
    for pos in "${keep_positions[@]}"; do
        if [[ "$i" -eq "$pos" ]]; then
            keep=true
            break
        fi
    done
    if [[ "$keep" == false ]]; then
        echo "Removing old backup: $(basename "${backups[$i]}")"
        rm -f "${backups[$i]}"
    fi
done

echo "Backups retained: $(ls -1 "$BACKUP_DIR"/backup-[0-9]*T[0-9]*Z.db.gz 2>/dev/null | wc -l)"
