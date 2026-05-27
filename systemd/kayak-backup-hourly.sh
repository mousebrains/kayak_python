#!/usr/bin/env bash
# Hourly SQLite backup with WAL checkpoint — RPO ≤ 1 hour.
#
# Runs via kayak-backup-hourly.timer (every hour at :38 with 2min jitter).
# Companion to kayak-backup-weekly.{sh,service,timer}; the weekly chains
# to kayak-backup-offsite.service for the long-term off-site copy. The
# hourly is local-only — its job is RPO, not durability.
#
# Filenames are hourly-YYYYMMDDTHHMMSSZ.db.gz (UTC, second-resolution).
# The hourly- prefix keeps the rotation glob orthogonal to the weekly's
# backup-* glob — the two cohabit /home/pat/backups without ever
# mistaking each other's files.
#
# `PRAGMA wal_checkpoint(TRUNCATE)` runs before .backup so the snapshot
# includes any uncommitted WAL frames. sqlite3 .backup is safe for a
# live DB (holds a read lock only).
#
# Retention: keep newest 24 .db.gz by mtime. ~80 MB compressed × 24 ≈
# 1.9 GB ceiling — fits on the Hetzner CPX21 disk envelope.

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

DB="${SQLITE_PATH:-${KAYAK_HOME}/DB/kayak.db}"
# /home/pat/backups, OUTSIDE the repo (review-4 R5.6): a git clean/checkout in
# the live editable tree must never reach backups. Weekly/offsite + the two
# .service ReadWritePaths use the same dir; SETUP.md provisions it before the
# timers run (ReadWritePaths needs it to exist at unit start).
BACKUP_DIR="${KAYAK_HOME}/backups"
KEEP=24
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$BACKUP_DIR/hourly-$STAMP.db"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB" ]]; then
    echo "Error: database not found at $DB" >&2
    exit 1
fi

# Clean up any uncompressed leftovers from a prior failed run *first*, so
# set -e doesn't skip this if today's gzip fails.
rm -f "$BACKUP_DIR"/hourly-[0-9]*T[0-9]*Z.db

# Checkpoint the WAL into the main DB so the .backup sees a fully-merged
# state. TRUNCATE shrinks the WAL file back to zero afterward.
sqlite3 "$DB" 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null

# Create the snapshot (safe for live DB — sqlite3 .backup holds a read lock)
sqlite3 "$DB" ".backup $DEST"
echo "Backed up to $DEST ($(du -h "$DEST" | cut -f1))"

# Compress with gzip level 4 (~82% reduction, fast)
gzip -4 "$DEST"
echo "Compressed to $DEST.gz ($(du -h "$DEST.gz" | cut -f1))"

# Retention: keep the newest $KEEP .db.gz files; remove the rest.
mapfile -t backups < <(ls -1r "$BACKUP_DIR"/hourly-[0-9]*T[0-9]*Z.db.gz 2>/dev/null)
for i in "${!backups[@]}"; do
    if (( i >= KEEP )); then
        echo "Removing old backup: $(basename "${backups[$i]}")"
        rm -f "${backups[$i]}"
    fi
done

echo "Hourly backups retained: $(ls -1 "$BACKUP_DIR"/hourly-[0-9]*T[0-9]*Z.db.gz 2>/dev/null | wc -l)"
