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

# Disk + swap warning thresholds. Override via env in the unit file or shell.
# "Aggressive" defaults — disk WARN well before the recap heartbeat reports it,
# swap WARN only when the swap _and_ free-RAM signals both fire (conjunction),
# so a one-off swap touch on an idle host doesn't page.
DISK_WARN_PCT="${DISK_WARN_PCT:-70}"
DISK_FAIL_PCT="${DISK_FAIL_PCT:-85}"
SWAP_USED_PCT_WARN="${SWAP_USED_PCT_WARN:-10}"
MEM_FREE_MB_WARN="${MEM_FREE_MB_WARN:-400}"

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

# Disk-usage check on /home (where ~/DB and ~/kayak/backups live).
DISK_PCT=$(df -P /home | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_PCT" -ge "$DISK_FAIL_PCT" ]; then
    DISK_HUMAN=$(df -h /home | tail -1 | awk '{print $3"/"$2" — "$4" free"}')
    echo "CRITICAL: /home at ${DISK_PCT}% (fail threshold ${DISK_FAIL_PCT}%) — ${DISK_HUMAN}"
    exit 2
fi
if [ "$DISK_PCT" -ge "$DISK_WARN_PCT" ]; then
    DISK_HUMAN=$(df -h /home | tail -1 | awk '{print $3"/"$2" — "$4" free"}')
    echo "WARNING: /home at ${DISK_PCT}% (warn threshold ${DISK_WARN_PCT}%) — ${DISK_HUMAN}"
    exit 1
fi

# Swap-usage check. Requires /proc/meminfo (the unit needs ProcSubset=all).
# Conjunction: (swap_used% high) AND (MemAvailable low) — either alone is fine.
if [ -r /proc/meminfo ]; then
    SWAP_TOTAL_KB=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
    SWAP_FREE_KB=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
    MEM_AVAIL_KB=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    if [ "${SWAP_TOTAL_KB:-0}" -gt 0 ]; then
        SWAP_USED_KB=$((SWAP_TOTAL_KB - SWAP_FREE_KB))
        SWAP_USED_PCT=$((SWAP_USED_KB * 100 / SWAP_TOTAL_KB))
        MEM_AVAIL_MB=$((MEM_AVAIL_KB / 1024))
        if [ "$SWAP_USED_PCT" -ge "$SWAP_USED_PCT_WARN" ] && [ "$MEM_AVAIL_MB" -lt "$MEM_FREE_MB_WARN" ]; then
            echo "WARNING: swap ${SWAP_USED_PCT}% used (${SWAP_USED_KB}/${SWAP_TOTAL_KB} kB) AND only ${MEM_AVAIL_MB} MB MemAvailable (<${MEM_FREE_MB_WARN} MB)"
            exit 1
        fi
    fi
fi

echo "OK: Latest observation ${AGE_HOURS}h ago ($LATEST), ${RECENT_COUNT} observations in last 2h, disk ${DISK_PCT}%"
exit 0
