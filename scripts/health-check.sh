#!/bin/bash
# Health check for the kayak pipeline.
#
# Verifies that the pipeline has run recently and that observations are
# flowing into the database. Exit codes:
#   0 = healthy
#   1 = stale data (no observations in threshold window)
#   2 = missing database or configuration error
#
# Usage:
#   ./scripts/health-check.sh [--max-age-hours N]
#
# Intended for cron monitoring, systemd ExecCondition, or external
# uptime checkers.

set -euo pipefail

MAX_AGE_HOURS="${1:-3}"
DB="${SQLITE_PATH:-${HOME}/DB/kayak.db}"

if [ ! -f "$DB" ]; then
    echo "CRITICAL: Database not found at $DB"
    exit 2
fi

# Check most recent observation timestamp
LATEST=$(sqlite3 "$DB" "
    SELECT MAX(observed_at) FROM latest_observation;
")

if [ -z "$LATEST" ]; then
    echo "CRITICAL: No observations in database"
    exit 2
fi

# Convert to epoch for comparison. SQLite stores observed_at in UTC with no
# TZ suffix, so parse explicitly as UTC — otherwise `date -d` treats it as
# local time and AGE_HOURS is off by the local UTC offset.
LATEST_EPOCH=$(date -u -d "$LATEST UTC" +%s 2>/dev/null || date -j -u -f "%Y-%m-%d %H:%M:%S" "$LATEST" +%s 2>/dev/null)
NOW_EPOCH=$(date +%s)
AGE_HOURS=$(( (NOW_EPOCH - LATEST_EPOCH) / 3600 ))

if [ "$AGE_HOURS" -gt "$MAX_AGE_HOURS" ]; then
    echo "WARNING: Latest observation is ${AGE_HOURS}h old (threshold: ${MAX_AGE_HOURS}h) — last: $LATEST"
    exit 1
fi

# Check pipeline timer status (if systemd is available)
if command -v systemctl &>/dev/null; then
    TIMER_STATE=$(systemctl is-active kayak-pipeline.timer 2>/dev/null || echo "unknown")
    if [ "$TIMER_STATE" != "active" ]; then
        echo "WARNING: kayak-pipeline.timer is $TIMER_STATE"
        exit 1
    fi
fi

# Count observations from last 2 hours as a throughput check
RECENT_COUNT=$(sqlite3 "$DB" "
    SELECT COUNT(*) FROM observation
    WHERE observed_at > datetime('now', '-2 hours');
")

echo "OK: Latest observation ${AGE_HOURS}h ago ($LATEST), ${RECENT_COUNT} observations in last 2h"
exit 0
