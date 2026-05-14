#!/usr/bin/env bash
# Upload the newest local DB backup to Google Drive (encrypted via rclone crypt).
#
# Triggered by kayak-backup-weekly.service via OnSuccess= — runs only
# after a successful weekly local backup. The hourly backup is local-
# only (no offsite chain). Failures here route through OnFailure= →
# kayak-notify-failure@.service for an email alert, but do NOT roll
# back the local backup.
#
# Retention: keep newest 26 off-host (~6 months of weekly), prune older.

set -euo pipefail

BACKUP_DIR="/home/pat/kayak/backups"
REMOTE="gdrive-crypt"
KEEP=26

mapfile -t backups < <(ls -1r "$BACKUP_DIR"/backup-[0-9]*T[0-9]*Z.db.gz 2>/dev/null)

if [[ ${#backups[@]} -eq 0 ]]; then
    echo "Error: no local backup found in $BACKUP_DIR" >&2
    exit 1
fi

NEWEST="${backups[0]}"

if ! rclone listremotes | grep -q "^${REMOTE}:$"; then
    echo "Error: rclone remote ${REMOTE}: not configured" >&2
    exit 1
fi

rclone copy "$NEWEST" "${REMOTE}:" --quiet
echo "Uploaded $(basename "$NEWEST") to ${REMOTE}:"

# Off-host retention: keep newest $KEEP, delete older
mapfile -t offsite < <(rclone lsf "${REMOTE}:" --include 'backup-*.db.gz' --files-only 2>/dev/null | sort -r)

for ((i=KEEP; i<${#offsite[@]}; i++)); do
    echo "Removing old off-host: ${offsite[$i]}"
    rclone delete "${REMOTE}:${offsite[$i]}"
done

remaining=$(rclone lsf "${REMOTE}:" --include 'backup-*.db.gz' --files-only 2>/dev/null | wc -l)
echo "Off-host backups retained: $remaining (cap: $KEEP)"
