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

# Per-source ALERT rate-limit. The check still runs as often as the timer fires
# (fast detection), but any one silent source pages at most once per this many
# days — the last-alert time per source is persisted in HEALTHCHECK_STATE_FILE
# (source_id<TAB>epoch). 0 disables the limit (alert every run, the old behavior).
SOURCE_ALERT_DAYS="${HEALTHCHECK_SOURCE_ALERT_DAYS:-7}"
case "$SOURCE_ALERT_DAYS" in
    ''|*[!0-9]*)
        echo "CRITICAL: HEALTHCHECK_SOURCE_ALERT_DAYS must be a non-negative integer (got '${SOURCE_ALERT_DAYS}')"
        exit 2
        ;;
esac
STATE_FILE="${HEALTHCHECK_STATE_FILE:-${KAYAK_HOME:-$HOME}/var/healthcheck-source-alerts.tsv}"

# Muted sources — comma-separated source ids known dead + acknowledged, fully
# excluded from the per-source check (no alert, not even the weekly reminder).
# Sanitize to a bare integer CSV so the value is safe to interpolate into SQL:
# strip anything but digits/commas, then collapse stray/edge commas.
MUTE_CLEAN="$(printf '%s' "${HEALTHCHECK_MUTE_SOURCES:-}" | tr -cd '0-9,' | sed 's/,\{2,\}/,/g; s/^,//; s/,$//')"
MUTE_CLAUSE=""
[ -n "$MUTE_CLEAN" ] && MUTE_CLAUSE="AND s.id NOT IN ($MUTE_CLEAN)"

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
# Tab-delimited (id<TAB>name<TAB>latest) so the rate-limit loop can read each
# source. Muted sources are excluded outright; the OR pair is parenthesized so the
# mute AND binds to the whole active-source predicate, not just the USGS arm.
STALE_SOURCES=$(sqlite3 "$DB" "
    SELECT s.id || char(9) || s.name || char(9) || COALESCE(MAX(lo.observed_at), 'NEVER')
    FROM source s
    JOIN gauge_source gs ON gs.source_id = s.id
    LEFT JOIN fetch_url fu ON fu.id = s.fetch_url_id
    LEFT JOIN latest_observation lo ON lo.source_id = s.id
    WHERE (fu.is_active = 1
        OR (s.agency = 'USGS' AND fu.is_active IS NOT 1))
      ${MUTE_CLAUSE}
    GROUP BY s.id
    HAVING MAX(lo.observed_at) < datetime('now', '-${STALE_SOURCE_DAYS} days')
        OR (fu.is_active = 1 AND MAX(lo.observed_at) IS NULL);
")

# Per-source alert rate-limit. For each currently-silent source, alert only if it
# hasn't alerted within SOURCE_ALERT_DAYS — so detection is as fast as the timer
# but any one source pages at most once a week. The new state holds ONLY the
# still-silent set, so a recovered source drops out (and re-alerts fresh if it
# dies again). Fail toward alerting: if the state can't be written we still page
# the due set rather than silently suppress.
DUE=""
SUPPRESSED=0
NOW_EPOCH=$(date +%s)
LIMIT_SECS=$((SOURCE_ALERT_DAYS * 86400))
NEW_STATE=""
while IFS=$'\t' read -r sid sname slatest; do
    [ -z "$sid" ] && continue
    last=0
    if [ -f "$STATE_FILE" ]; then
        last=$(awk -F'\t' -v s="$sid" '$1==s {print $2; exit}' "$STATE_FILE")
        [ -z "$last" ] && last=0
    fi
    if [ "$((NOW_EPOCH - last))" -ge "$LIMIT_SECS" ]; then
        DUE="${DUE}  ${sid} ${sname} latest=${slatest}
"
        NEW_STATE="${NEW_STATE}${sid}	${NOW_EPOCH}
"
    else
        SUPPRESSED=$((SUPPRESSED + 1))
        NEW_STATE="${NEW_STATE}${sid}	${last}
"
    fi
done <<EOF
$STALE_SOURCES
EOF

# ALWAYS rebuild the state to the current silent set — even when it's empty (all
# sources recovered) — so a recovered source's old entry is pruned, not reused to
# suppress a later re-death. Empty set → remove the file. Fail toward alerting:
# a write failure leaves the due set to page rather than silently suppress.
if [ -n "$NEW_STATE" ]; then
    mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true
    if ! { printf '%s' "$NEW_STATE" > "$STATE_FILE.tmp" 2>/dev/null \
        && mv "$STATE_FILE.tmp" "$STATE_FILE" 2>/dev/null; }; then
        echo "WARNING: could not write healthcheck state $STATE_FILE — per-source alert rate-limit not persisted"
    fi
else
    rm -f "$STATE_FILE" 2>/dev/null || true
fi

if [ -n "$DUE" ]; then
    DUE_COUNT=$(printf '%s' "$DUE" | grep -c .)
    echo "WARNING: ${DUE_COUNT} active source(s) with no observation in ${STALE_SOURCE_DAYS} days (each alerts at most once per ${SOURCE_ALERT_DAYS}d):"
    printf '%s' "$DUE"
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

# Keep silenced-but-still-watched sources visible on the green line so a parked
# feed never becomes an invisible blind spot: suppressed = silent within its
# weekly alert window; muted = acknowledged-dead + fully excluded.
OK_EXTRA=""
[ "$SUPPRESSED" -gt 0 ] && OK_EXTRA="${OK_EXTRA}, ${SUPPRESSED} silent source(s) within their ${SOURCE_ALERT_DAYS}d alert window"
[ -n "$MUTE_CLEAN" ] && OK_EXTRA="${OK_EXTRA}, muted sources: ${MUTE_CLEAN}"
echo "OK: Latest observation ${AGE_HOURS}h ago ($LATEST), ${RECENT_COUNT} observations in last 2h, no source due to alert (>${STALE_SOURCE_DAYS}d silent)${OK_EXTRA}, disk ${DISK_PCT}%"
exit 0
