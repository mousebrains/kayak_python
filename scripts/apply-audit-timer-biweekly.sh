#!/usr/bin/env bash
# Apply the bi-weekly kayak-audit-gauges.timer schedule to live systemd.
#
# Bumps the audit cadence from "every other month on the 15th" to "1st
# and 15th of every month at 03:00" — 26 runs/year. The 14-day gaps
# match the audit script's --days 14 window, eliminating the coverage
# hole the bi-monthly schedule left.
#
# Usage:
#   sudo bash scripts/apply-audit-timer-biweekly.sh
#
# Requires sudo for the cp / daemon-reload / restart steps.

set -euo pipefail

REPO_UNIT="/home/pat/kayak/systemd/kayak-audit-gauges.timer"
LIVE_UNIT="/etc/systemd/system/kayak-audit-gauges.timer"

if [[ ! -r "$REPO_UNIT" ]]; then
    echo "ERROR: $REPO_UNIT not readable" >&2
    exit 1
fi

echo "=== diff (repo - live) ==="
if diff "$REPO_UNIT" "$LIVE_UNIT"; then
    echo "(no diff — nothing to apply, exiting)"
    exit 0
fi
echo ""

read -r -p "Apply the diff above to $LIVE_UNIT and reload systemd? [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "aborted"
    exit 1
fi

cp "$REPO_UNIT" "$LIVE_UNIT"
echo "copied → $LIVE_UNIT"

systemctl daemon-reload
echo "systemd reloaded"

# Restarting the timer makes systemd recompute the next-firing time
# against the new OnCalendar without disabling/re-enabling.
systemctl restart kayak-audit-gauges.timer
echo "timer restarted"

echo ""
echo "=== new schedule (next 4 fires) ==="
systemctl list-timers kayak-audit-gauges.timer --all
echo ""
systemd-analyze calendar '*-*-01,15 03:00' --iterations 4 | grep -E 'Next elapse|Iteration'
