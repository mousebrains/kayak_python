#!/usr/bin/env bash
# Weekly SQLite backup with retention policy.
#
# Keeps at most 4 backups: the newest, plus those at positions
# 1, 3, and 5 in the sorted list (roughly 1, 3, and 5 weeks old).
#
# Uses sqlite3 .backup for a consistent snapshot of a live database.
# Backups are gzip-compressed (level 4, ~82% size reduction).

set -euo pipefail

DB="${SQLITE_PATH:-/home/pat/DB/kayak.db}"
BACKUP_DIR="/home/pat/kayak/backups"
DATE=$(date +%Y%m%d)
DEST="$BACKUP_DIR/kayak-$DATE.db"

mkdir -p "$BACKUP_DIR"

# Create backup (safe for live DB — sqlite3 .backup holds a read lock)
if [[ ! -f "$DB" ]]; then
    echo "Error: database not found at $DB" >&2
    exit 1
fi

sqlite3 "$DB" ".backup $DEST"
echo "Backed up to $DEST ($(du -h "$DEST" | cut -f1))"

# Compress with gzip level 4 (~82% reduction, fast)
gzip -4 "$DEST"
echo "Compressed to $DEST.gz ($(du -h "$DEST.gz" | cut -f1))"

# Retention: keep backups at positions 0, 1, 3, 5 (newest first)
# This gives coverage at 0, ~1, ~3, ~5 weeks back
keep_positions=(0 1 3 5)

mapfile -t backups < <(ls -1r "$BACKUP_DIR"/kayak-[0-9]*.db.gz 2>/dev/null)

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

# Clean up any old uncompressed backups
rm -f "$BACKUP_DIR"/kayak-[0-9]*.db

echo "Backups retained: $(ls -1 "$BACKUP_DIR"/kayak-[0-9]*.db.gz 2>/dev/null | wc -l)"
