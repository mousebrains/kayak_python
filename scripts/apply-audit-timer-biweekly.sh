#!/usr/bin/env bash
# Apply the bi-weekly kayak-audit-gauges schedule to live systemd.
#
# Updates both the .timer (OnCalendar=*-*-02,17 03:00) and the .service
# (--days 16) so the audit's lookback window always meets or exceeds
# the max calendar gap (16 days after 31-day months); events near a
# boundary are flagged at two consecutive fires.
#
# Usage:
#   sudo bash scripts/apply-audit-timer-biweekly.sh
#
# Requires sudo for the cp / daemon-reload / restart steps.

set -euo pipefail

REPO_DIR="/home/pat/kayak/systemd"
LIVE_DIR="/etc/systemd/system"
UNITS=(kayak-audit-gauges.timer kayak-audit-gauges.service)

for unit in "${UNITS[@]}"; do
    if [[ ! -r "$REPO_DIR/$unit" ]]; then
        echo "ERROR: $REPO_DIR/$unit not readable" >&2
        exit 1
    fi
done

any_diff=0
for unit in "${UNITS[@]}"; do
    echo "=== diff (repo - live) for $unit ==="
    if diff "$REPO_DIR/$unit" "$LIVE_DIR/$unit"; then
        echo "(no diff)"
    else
        any_diff=1
    fi
    echo ""
done

if [[ $any_diff -eq 0 ]]; then
    echo "Nothing to apply, exiting."
    exit 0
fi

read -r -p "Apply the diff(s) above to $LIVE_DIR and reload systemd? [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "aborted"
    exit 1
fi

for unit in "${UNITS[@]}"; do
    cp "$REPO_DIR/$unit" "$LIVE_DIR/$unit"
    echo "copied → $LIVE_DIR/$unit"
done

systemctl daemon-reload
echo "systemd reloaded"

systemctl restart kayak-audit-gauges.timer
echo "timer restarted"

echo ""
echo "=== new schedule ==="
systemctl list-timers kayak-audit-gauges.timer --all
echo ""
systemd-analyze calendar '*-*-02,17 03:00' --iterations 4 | grep -E 'Next elapse|Iteration'
