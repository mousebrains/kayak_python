#!/usr/bin/env bash
# Install/update kayak systemd service and timer files.
# Usage: sudo ./systemd/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST=/etc/systemd/system
UNITS=(kayak-pipeline.service kayak-pipeline.timer kayak-decimate.service kayak-decimate.timer kayak-editor-retention.service kayak-editor-retention.timer kayak-backup-weekly.service kayak-backup-weekly.timer kayak-backup-hourly.service kayak-backup-hourly.timer kayak-backup-offsite.service kayak-notify-failure@.service kayak-heartbeat.service kayak-heartbeat.timer kayak-healthcheck.service kayak-healthcheck.timer kayak-audit-gauges.service kayak-audit-gauges.timer kayak-metadata-snapshot.service kayak-metadata-snapshot.timer kayak-cert-expiry.service kayak-cert-expiry.timer kayak-cert-renewal-test.service kayak-cert-renewal-test.timer kayak-config-drift.service kayak-config-drift.timer)
TIMERS=(kayak-pipeline.timer kayak-decimate.timer kayak-editor-retention.timer kayak-backup-weekly.timer kayak-backup-hourly.timer kayak-heartbeat.timer kayak-healthcheck.timer kayak-audit-gauges.timer kayak-metadata-snapshot.timer kayak-cert-expiry.timer kayak-cert-renewal-test.timer kayak-config-drift.timer)

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root (use sudo)" >&2
    exit 1
fi

changed=0

for unit in "${UNITS[@]}"; do
    src="$SCRIPT_DIR/$unit"
    dst="$DEST/$unit"

    if [[ ! -f "$src" ]]; then
        echo "Error: $src not found" >&2
        exit 1
    fi

    if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
        echo "  unchanged: $unit"
    else
        cp "$src" "$dst"
        echo "  updated:   $unit"
        changed=1
    fi
done

if [[ $changed -eq 1 ]]; then
    echo ""
    echo "Reloading systemd daemon..."
    systemctl daemon-reload
fi

for timer in "${TIMERS[@]}"; do
    if ! systemctl is-enabled --quiet "$timer" 2>/dev/null; then
        echo "  enabling:  $timer"
        systemctl enable "$timer"
    fi
    if ! systemctl is-active --quiet "$timer" 2>/dev/null; then
        echo "  starting:  $timer"
        systemctl start "$timer"
    fi
done

echo ""
if [[ $changed -eq 1 ]]; then
    echo "Done — units updated and reloaded."
else
    echo "Done — nothing to update."
fi

echo ""
echo "Timer status:"
systemctl list-timers "${TIMERS[@]}" --no-pager
