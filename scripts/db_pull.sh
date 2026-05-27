#!/usr/bin/env bash
# Pull a consistent snapshot of the live kayak database to ../DB/
#
# Creates a compressed snapshot on the remote using `sqlite3 .backup` (safe
# against concurrent writes), rsyncs it here, unpacks it to ../DB/kayak.db,
# and records the snapshot name so db_push.sh can reconcile observations
# that accumulate on live while we work.
#
# Both the remote backup dir and the local ../DB keep only the newest
# KEEP_PULL_SNAPSHOTS (default 3) kayak-<TS>.db.gz snapshots; older ones are
# pruned each run so pulls don't accumulate unbounded. The systemd
# hourly/weekly backup units remain the real backup rotation.
#
# Usage:  ./scripts/db_pull.sh [-f]
#   -f   overwrite an existing local ../DB/kayak.db without prompting
#   env: KEEP_PULL_SNAPSHOTS  newest pull snapshots to keep (default 3)

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

REMOTE_HOST="${REMOTE_HOST:-pat@levels.mousebrains.com}"
REMOTE_DB="${REMOTE_DB:-${KAYAK_HOME}/DB/kayak.db}"
REMOTE_BACKUP_DIR="${REMOTE_BACKUP_DIR:-${KAYAK_HOME}/backups}"
KEEP_PULL_SNAPSHOTS="${KEEP_PULL_SNAPSHOTS:-3}"
# Guard: a non-numeric value would make (( i >= KEEP )) treat it as 0 and prune
# every snapshot — fall back to the default rather than delete all of them.
[[ "$KEEP_PULL_SNAPSHOTS" =~ ^[0-9]+$ ]] || KEEP_PULL_SNAPSHOTS=3

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
ssh "${REMOTE_HOST}" "bash -s '${REMOTE_DB}' '${REMOTE_SNAPSHOT}' '${KEEP_PULL_SNAPSHOTS}'" <<'REMOTE'
set -euo pipefail
DB="$1"
DEST="$2"
KEEP="$3"
BACKUP_DIR="$(dirname "$DEST")"
mkdir -p "$BACKUP_DIR"
sqlite3 "$DB" ".backup '$DEST'"
gzip -9 "$DEST"
ls -lh "${DEST}.gz"
# Retention: keep the newest $KEEP pull snapshots; remove older. The anchored
# glob matches only kayak-<TS>.db.gz — never kayak-from-local-/kayak-replaced-.
mapfile -t snaps < <(ls -1r "$BACKUP_DIR"/kayak-[0-9]*T[0-9]*Z.db.gz 2>/dev/null)
for i in "${!snaps[@]}"; do
    if (( i >= KEEP )); then
        echo "Removing old pull snapshot: $(basename "${snaps[$i]}")"
        rm -f "${snaps[$i]}"
    fi
done
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

# Retention: keep the newest $KEEP_PULL_SNAPSHOTS pulled .gz snapshots locally
# too; the current pull is the newest, so it is always kept.
mapfile -t local_snaps < <(ls -1r "${LOCAL_DB_DIR}"/kayak-[0-9]*T[0-9]*Z.db.gz 2>/dev/null)
for i in "${!local_snaps[@]}"; do
    if (( i >= KEEP_PULL_SNAPSHOTS )); then
        echo "Removing old local snapshot: $(basename "${local_snaps[$i]}")"
        rm -f "${local_snaps[$i]}"
    fi
done

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
