#!/usr/bin/env bash
# Pull a consistent snapshot of the live kayak database to ../DB/
#
# Creates a compressed snapshot on the remote using `sqlite3 .backup` (safe
# against concurrent writes), rsyncs it here, unpacks it to ../DB/kayak.db,
# and records the snapshot name so db_push.sh can reconcile observations
# that accumulate on live while we work.
#
# Usage:  ./scripts/db_pull.sh [-f]
#   -f   overwrite an existing local ../DB/kayak.db without prompting

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-pat@levels.mousebrains.com}"
REMOTE_DB="${REMOTE_DB:-/home/pat/DB/kayak.db}"
REMOTE_BACKUP_DIR="${REMOTE_BACKUP_DIR:-/home/pat/kayak/backups}"

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

mkdir -p "${LOCAL_DB_DIR}"

if [[ -f "${LOCAL_DB}" && "${FORCE}" != true ]]; then
    read -rp "Overwrite existing ${LOCAL_DB}? [y/N] " confirm
    [[ "${confirm}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
SNAPSHOT="kayak-${TS}.db"
REMOTE_SNAPSHOT="${REMOTE_BACKUP_DIR}/${SNAPSHOT}"

echo "=== Creating snapshot on ${REMOTE_HOST} ==="
ssh "${REMOTE_HOST}" "bash -s '${REMOTE_DB}' '${REMOTE_SNAPSHOT}'" <<'REMOTE'
set -euo pipefail
DB="$1"
DEST="$2"
mkdir -p "$(dirname "$DEST")"
sqlite3 "$DB" ".backup '$DEST'"
gzip -9 "$DEST"
ls -lh "${DEST}.gz"
REMOTE

echo ""
echo "=== Pulling snapshot to ${LOCAL_DB_DIR} ==="
rsync -avP "${REMOTE_HOST}:${REMOTE_SNAPSHOT}.gz" "${LOCAL_DB_DIR}/"

echo ""
echo "=== Unpacking ==="
rm -f "${LOCAL_DB}" "${LOCAL_DB}-wal" "${LOCAL_DB}-shm"
gunzip -c "${LOCAL_DB_DIR}/${SNAPSHOT}.gz" > "${LOCAL_DB}"

# Handshake with db_push.sh: records which snapshot this local DB came from.
echo "${SNAPSHOT}" > "${LOCAL_DB_DIR}/.pulled_snapshot"
echo "${TS}" > "${LOCAL_DB_DIR}/.pulled_snapshot_ts"

echo ""
echo "=== Summary ==="
sqlite3 "${LOCAL_DB}" <<'SQL'
SELECT 'Reaches:       ' || count(*) FROM reach;
SELECT 'Sources:       ' || count(*) FROM source;
SELECT 'Gauges:        ' || count(*) FROM gauge;
SELECT 'Observations:  ' || count(*) FROM observation;
SELECT 'Latest max:    ' || COALESCE(MAX(observed_at), '(none)') FROM observation;
SQL

echo ""
echo "Local DB:       ${LOCAL_DB}"
echo "Snapshot name:  ${SNAPSHOT}"
echo "Done."
