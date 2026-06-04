#!/bin/bash
# Health check for the kayak pipeline.
#
# Verifies that the pipeline has run recently and that observations are
# flowing into the database — globally (the pipeline writes *something*)
# and per source (no individual feed has gone dark; docs/slo.md SLO F).
# Exit codes:
#   0 = healthy
#   1 = stale data (global or per-source threshold exceeded)
#   2 = missing database or configuration error
#
# Usage:
#   ./scripts/health-check.sh [MAX_AGE_HOURS]   # positional; default 3
#
# Intended for cron monitoring, systemd ExecCondition, or external
# uptime checkers.

set -euo pipefail

MAX_AGE_HOURS="${1:-3}"
DB="${SQLITE_PATH:-${HOME}/DB/kayak.db}"

# Per-source dead-feed window. There is no per-source cadence model —
# feeds update anywhere from every 15 min to a few times a day — so this
# is a deliberately coarse "feed died" detector, not a lag detector.
STALE_SOURCE_DAYS="${STALE_SOURCE_DAYS:-14}"
case "$STALE_SOURCE_DAYS" in
    ''|*[!0-9]*)
        # A non-numeric value would render the SQL datetime() NULL and
        # silently disable the check — fail loudly instead.
        echo "CRITICAL: STALE_SOURCE_DAYS must be a non-negative integer (got '${STALE_SOURCE_DAYS}')"
        exit 2
        ;;
esac

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

# Per-source liveness. The global MAX above goes green as long as ANY
# source keeps writing, so a single dead feed (or a source that never
# produced data at all) is invisible to it. Two scopes, both gauge-linked:
#   - fetch-backed (fetch_url.is_active = 1): must have at least one
#     observation, none older than STALE_SOURCE_DAYS;
#   - OGC-fetched USGS (agency = 'USGS' without an active fetch_url —
#     `levels fetch-usgs-ogc` selects ALL gauge-linked USGS sources via
#     the gauge link, ignoring fetch_url entirely): checked for going
#     silent > STALE_SOURCE_DAYS, but never-fed ones are EXEMPT —
#     they're speculative metadata additions awaiting upstream OGC
#     coverage, not feeds that died (operator decision 2026-06-03).
#     `fu.is_active IS NOT 1` (not `= 0`) so the no-fetch_url-row NULL
#     lands in this arm too — plain NOT(=1) would 3-value-NULL it out.
#
# Scope notes:
#   - JOIN gauge_source: active fetch-backed sources with NO gauge link
#     are orphans — `levels orphan-check` (pipeline step 7) already
#     alerts on those; flagging them here would double-report.
#   - Calc-backed sources (calc_expression; non-USGS, no fetch_url) are
#     excluded: they derive from fetched inputs, so dead inputs surface
#     here anyway.
#   - A source added by deploy is flagged until the next pipeline tick
#     fetches it (deploy runs build, not fetch) — one transient alert,
#     self-heals within the hour; persistent = the feed really is dead.
#   - fu.is_active appears non-aggregated under GROUP BY s.id (SQLite's
#     bare-column extension) — safe because each source has exactly one
#     fetch_url row, so is_active is functionally dependent on s.id.
#     Don't "fix" it to MAX(fu.is_active) or port to a stricter engine
#     unexamined.
STALE_SOURCES=$(sqlite3 "$DB" "
    SELECT s.id || ' ' || s.name || ' latest=' || COALESCE(MAX(lo.observed_at), 'NEVER')
    FROM source s
    JOIN gauge_source gs ON gs.source_id = s.id
    LEFT JOIN fetch_url fu ON fu.id = s.fetch_url_id
    LEFT JOIN latest_observation lo ON lo.source_id = s.id
    WHERE fu.is_active = 1
       OR (s.agency = 'USGS' AND fu.is_active IS NOT 1)
    GROUP BY s.id
    HAVING MAX(lo.observed_at) < datetime('now', '-${STALE_SOURCE_DAYS} days')
        OR (fu.is_active = 1 AND MAX(lo.observed_at) IS NULL);
")
if [ -n "$STALE_SOURCES" ]; then
    STALE_COUNT=$(printf '%s\n' "$STALE_SOURCES" | wc -l | tr -d ' ')
    echo "WARNING: ${STALE_COUNT} active source(s) with no observation in ${STALE_SOURCE_DAYS} days:"
    printf '%s\n' "$STALE_SOURCES"
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

# Disk-usage check on /home (where ~/DB and ~/backups live).
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

echo "OK: Latest observation ${AGE_HOURS}h ago ($LATEST), ${RECENT_COUNT} observations in last 2h, no active source silent >${STALE_SOURCE_DAYS}d, disk ${DISK_PCT}%"
exit 0
