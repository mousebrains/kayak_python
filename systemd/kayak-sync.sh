#!/usr/bin/env bash
# Sync observations from legacy MySQL (levels_data) into local SQLite.
#
# Opens an SSH tunnel to reach the MySQL server on DreamHost,
# runs sync_legacy_observations.py, then tears down the tunnel.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="/home/pat/.venv"
TARGET_DB="sqlite:////home/pat/DB/kayak.db"
LEGACY_URL="mysql+pymysql://levels:Deschutes@127.0.0.1:3307/levels_data"

SSH_USER="tpw@levels.wkcc.org"
SSH_REMOTE="mysql.wkcc.dreamhosters.com:3306"
LOCAL_PORT=3307
SOCK="/tmp/kayak-sync-ssh-$$"

cleanup() {
    if [[ -S "$SOCK" ]]; then
        echo "Closing SSH tunnel..."
        ssh -S "$SOCK" -O exit "$SSH_USER" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# Open SSH tunnel with control socket
echo "Opening SSH tunnel (local :$LOCAL_PORT → $SSH_REMOTE)..."
ssh -f -N -M -S "$SOCK" \
    -L "$LOCAL_PORT:$SSH_REMOTE" \
    -o ExitOnForwardFailure=yes \
    "$SSH_USER"

# Wait for the tunnel to be ready
echo "Waiting for tunnel..."
for i in $(seq 1 30); do
    if (echo >/dev/tcp/127.0.0.1/$LOCAL_PORT) 2>/dev/null; then
        echo "Tunnel ready."
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "Error: tunnel not ready after 30s" >&2
        exit 1
    fi
    sleep 1
done

# Run the sync
echo "Running observation sync..."
"$VENV/bin/python3" "$REPO_DIR/scripts/sync_legacy_observations.py" \
    --legacy "$LEGACY_URL" \
    --target "$TARGET_DB" \
    --verbose

echo "Sync complete."
