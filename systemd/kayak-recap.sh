#!/usr/bin/env bash
# Email the operator a weekly pipeline-activity recap.
#
# Wraps scripts/recap.py — pulls the last 7 days of structured events
# from journald and renders a per-step ok/failed/skipped tally plus
# the latest failure message per step. Companion to kayak-heartbeat
# (which reports "host alive"); this one answers "did the pipeline
# do useful work this week?".
#
# Invoked weekly by kayak-recap.timer. Safe to run by hand.

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

HOST=$(hostname)
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
DAYS=${RECAP_DAYS:-7}
SUBJECT="Kayak levels recap — $HOST $(date +%Y-%m-%d) (last ${DAYS}d)"

# scripts/recap.py exits 0 even with no events; the only failure path
# is journalctl missing (handled inside the script with exit 2).
RECAP=$("${KAYAK_HOME}/.venv/bin/python3" "${KAYAK_HOME}/kayak/scripts/recap.py" \
    --days "$DAYS" --unit 'kayak-*' 2>&1)

BODY=$(cat <<EOF
Kayak levels pipeline activity recap

Sent:    $TS
Host:    $HOST
Window:  last ${DAYS} day(s)

$RECAP

---
Source: structured events from kayak.utils.struct_log under all
kayak-* systemd units, parsed by scripts/recap.py. If "Events
parsed: 0" — either no kayak-* units have run yet in the window, or
the structured-log scaffold hasn't been deployed to those units.
EOF
)

echo "$BODY" | mail -s "$SUBJECT" pat.kayak@gmail.com
echo "$BODY" | logger -t kayak-recap -p user.info
