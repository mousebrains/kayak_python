#!/usr/bin/env bash
# Push a locally-edited kayak DB back to the live system, preserving every
# observation the live pipeline collected while we were editing locally.
#
# Strategy:
#   1. Snapshot local DB, ship it to remote as kayak-from-local-<ts>.db.gz.
#   2. On remote: stop pipeline timers, take a final snapshot of the live DB,
#      unpack our uploaded DB, merge live's observations + cache tables into
#      our DB (metadata stays local-wins), then atomically swap into
#      ~/DB/kayak.db and restart the timers.
#   3. Clean up: the consumed remote staging gz is removed, and the pre-push
#      live DB archived to kayak-replaced-<ts>.db.gz is pruned to the newest
#      KEEP_PUSH_ARCHIVES (default 5).
#
# Usage:  ./scripts/db_push.sh [-f]
#   -f   skip the interactive confirmation prompt
#   env: KEEP_PUSH_ARCHIVES  newest pre-push archives to keep (default 5)

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

REMOTE_HOST="${REMOTE_HOST:-pat@levels.mousebrains.com}"
REMOTE_DB="${REMOTE_DB:-${KAYAK_HOME}/DB/kayak.db}"
REMOTE_BACKUP_DIR="${REMOTE_BACKUP_DIR:-${KAYAK_HOME}/kayak/backups}"
KEEP_PUSH_ARCHIVES="${KEEP_PUSH_ARCHIVES:-5}"
# Guard: a non-numeric value would make (( i >= KEEP )) treat it as 0 and prune
# every archive — fall back to the default rather than delete all of them.
[[ "$KEEP_PUSH_ARCHIVES" =~ ^[0-9]+$ ]] || KEEP_PUSH_ARCHIVES=5

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCAL_DB_DIR="$(cd "${REPO_DIR}/.." && pwd)/DB"
LOCAL_DB="${LOCAL_DB_DIR}/kayak.db"

FORCE=false
while getopts "f" opt; do
    case $opt in
        f) FORCE=true ;;
        *) echo "Usage: $0 [-f]" >&2; exit 1 ;;
    esac
done

if [[ ! -f "${LOCAL_DB}" ]]; then
    echo "Error: ${LOCAL_DB} not found. Run scripts/db_pull.sh first." >&2
    exit 1
fi

if [[ ! -f "${LOCAL_DB_DIR}/.pulled_snapshot" ]]; then
    echo "Error: no pull handshake found at ${LOCAL_DB_DIR}/.pulled_snapshot." >&2
    echo "       This DB was not produced by scripts/db_pull.sh — refusing" >&2
    echo "       to push because the observation-merge step would be unsafe." >&2
    exit 1
fi

PULL_SNAPSHOT=$(cat "${LOCAL_DB_DIR}/.pulled_snapshot")

if [[ "${FORCE}" != true ]]; then
    echo "About to push ${LOCAL_DB} → ${REMOTE_HOST}:${REMOTE_DB}"
    echo "  Source snapshot: ${PULL_SNAPSHOT}"
    echo "  Live observations since pull will be merged in (preserved)."
    echo "  Metadata on live (reach/source/gauge/rating/etc.) will be overwritten."
    read -rp "Proceed? [y/N] " confirm
    [[ "${confirm}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
STAGED_GZ="${LOCAL_DB_DIR}/kayak-from-local-${TS}.db.gz"
REMOTE_STAGED_GZ="${REMOTE_BACKUP_DIR}/kayak-from-local-${TS}.db.gz"

echo ""
echo "=== Preparing local snapshot ==="
sqlite3 "${LOCAL_DB}" 'PRAGMA wal_checkpoint(TRUNCATE);'
STAGED_DB="${LOCAL_DB_DIR}/kayak-from-local-${TS}.db"
sqlite3 "${LOCAL_DB}" ".backup '${STAGED_DB}'"
gzip -9 "${STAGED_DB}"   # produces STAGED_GZ
ls -lh "${STAGED_GZ}"

echo ""
echo "=== Uploading to ${REMOTE_HOST} ==="
ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_BACKUP_DIR}'"
rsync -avP "${STAGED_GZ}" "${REMOTE_HOST}:${REMOTE_STAGED_GZ}"

echo ""
echo "=== Performing swap-and-merge on ${REMOTE_HOST} ==="
ssh "${REMOTE_HOST}" \
    "bash -s '${TS}' '${REMOTE_DB}' '${REMOTE_BACKUP_DIR}' '${REMOTE_STAGED_GZ}' '${KEEP_PUSH_ARCHIVES}'" <<'REMOTE'
set -euo pipefail
TS="$1"
DB="$2"
BACKUP_DIR="$3"
UPLOADED_GZ="$4"
KEEP="$5"

LIVE_FINAL="/tmp/kayak-live-final-${TS}.db"
NEW_DB="/tmp/kayak-new-${TS}.db"
REPLACED_GZ="${BACKUP_DIR}/kayak-replaced-${TS}.db"

echo "--- Stopping pipeline timers ---"
for unit in kayak-pipeline.timer kayak-decimate.timer \
            kayak-backup-weekly.timer kayak-backup-hourly.timer \
            kayak-pipeline.service kayak-decimate.service \
            kayak-backup-weekly.service kayak-backup-hourly.service; do
    sudo -n systemctl stop "$unit" 2>/dev/null || true
done

echo "--- Checkpointing and snapshotting live DB ---"
sqlite3 "$DB" 'PRAGMA wal_checkpoint(TRUNCATE);'
sqlite3 "$DB" ".backup '$LIVE_FINAL'"

echo "--- Unpacking uploaded DB ---"
gunzip -c "$UPLOADED_GZ" > "$NEW_DB"

echo "--- Merging live observations + caches into new DB ---"
sqlite3 "$NEW_DB" <<SQL
PRAGMA foreign_keys = OFF;
ATTACH DATABASE '${LIVE_FINAL}' AS live;
BEGIN;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT o.source_id, o.observed_at, o.data_type, o.value
    FROM live.observation AS o
    JOIN main.source AS s ON s.id = o.source_id;
DELETE FROM latest_observation;
INSERT INTO latest_observation
    SELECT lo.*
    FROM live.latest_observation AS lo
    JOIN main.source AS s ON s.id = lo.source_id;
DELETE FROM latest_gauge_observation;
INSERT INTO latest_gauge_observation
    SELECT lgo.*
    FROM live.latest_gauge_observation AS lgo
    JOIN main.gauge AS g ON g.id = lgo.gauge_id;
DELETE FROM pages;
COMMIT;
DETACH DATABASE live;
PRAGMA foreign_keys = ON;
SQL

echo "--- Integrity check on merged DB ---"
integrity=$(sqlite3 "$NEW_DB" 'PRAGMA integrity_check;')
if [[ "$integrity" != "ok" ]]; then
    echo "Integrity check failed: $integrity" >&2
    echo "Live DB is untouched. Staged file: $NEW_DB" >&2
    for unit in kayak-pipeline.timer kayak-decimate.timer \
                kayak-backup-weekly.timer kayak-backup-hourly.timer; do
        sudo -n systemctl start "$unit"
    done
    exit 1
fi
echo "  ok"

echo "--- Archiving outgoing live DB ---"
mv "$DB" "$REPLACED_GZ"
gzip -9 "$REPLACED_GZ"
rm -f "${DB}-wal" "${DB}-shm"

echo "--- Installing new DB ---"
mv "$NEW_DB" "$DB"
chmod 660 "$DB"

echo "--- Pruning sync artifacts ---"
# The uploaded staging gz was unpacked into $NEW_DB and installed — remove it.
rm -f "$UPLOADED_GZ"
# Retention: keep the newest $KEEP pre-push archives; remove older. The anchored
# glob matches only kayak-replaced-<TS>.db.gz.
mapfile -t archives < <(ls -1r "$BACKUP_DIR"/kayak-replaced-[0-9]*T[0-9]*Z.db.gz 2>/dev/null)
for i in "${!archives[@]}"; do
    if (( i >= KEEP )); then
        echo "Removing old pre-push archive: $(basename "${archives[$i]}")"
        rm -f "${archives[$i]}"
    fi
done

echo "--- Restarting timers ---"
for unit in kayak-pipeline.timer kayak-decimate.timer \
            kayak-backup-weekly.timer kayak-backup-hourly.timer; do
    sudo -n systemctl start "$unit"
done

echo "--- Summary ---"
sqlite3 "$DB" <<'SUMMARY'
SELECT 'Reaches:       ' || count(*) FROM reach;
SELECT 'Sources:       ' || count(*) FROM source;
SELECT 'Gauges:        ' || count(*) FROM gauge;
SELECT 'Observations:  ' || count(*) FROM observation;
SELECT 'Latest max:    ' || COALESCE(MAX(observed_at), '(none)') FROM observation;
SUMMARY

rm -f "$LIVE_FINAL"
echo ""
echo "Replaced live DB archived at: ${REPLACED_GZ}.gz"
echo "Done."
REMOTE

echo ""
echo "=== Cleanup ==="
rm -f "${STAGED_GZ}"
echo "Done."
