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

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

# S8 (Batch 4): backup policy is host configuration. These knobs come from
# /etc/kayak/env (KAYAK_BACKUP_DIR / KAYAK_OFFSITE_REMOTE / KAYAK_OFFSITE_KEEP,
# schema of record: kayak.host.HostConfig); the fallbacks mirror HostConfig's
# defaults so an env-less host behaves exactly as before.
BACKUP_DIR="${KAYAK_BACKUP_DIR:-${KAYAK_HOME}/backups}"  # out of the repo (review-4 R5.6)
REMOTE="${KAYAK_OFFSITE_REMOTE:-gdrive-crypt}"
KEEP="${KAYAK_OFFSITE_KEEP:-26}"

# Fail-closed knob validation BEFORE any rclone copy/delete (PR #189 review
# P1): KEEP=0 (or garbage) would otherwise start the prune loop at index 0
# and delete EVERY offsite backup, including the one just uploaded. The
# deploy gate (validate-config) checks the same invariants, but a timer run
# must not trust that the env was deployed through the gate.
if ! [[ "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: KAYAK_OFFSITE_KEEP must be a positive integer (got '${KEEP}')" >&2
    exit 1
fi
if [[ "$REMOTE" == *:* || -z "$REMOTE" ]]; then
    echo "Error: KAYAK_OFFSITE_REMOTE must be a bare rclone remote name, no colon (got '${REMOTE}')" >&2
    exit 1
fi
if [[ "$BACKUP_DIR" != /* ]]; then
    echo "Error: KAYAK_BACKUP_DIR must be an absolute path (got '${BACKUP_DIR}')" >&2
    exit 1
fi

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
