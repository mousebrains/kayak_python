#!/usr/bin/env bash
# Compose a short "host alive" status and email it to the maintainer.
# Purpose: positive signal that the alert pipeline (msmtp -> Gmail) still
# works. If the heartbeat stops arriving, something is broken — invert
# the usual "only hear on failure" failure mode.
#
# Invoked weekly by kayak-heartbeat.timer. Safe to run by hand.

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

HOST=$(hostname)
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# `uptime -p` reads /proc/uptime which ProcSubset=pid hides. Use the
# kernel boot timestamp from systemctl (D-Bus, no /proc required).
BOOTED=$(systemctl show -p KernelTimestamp --value)
DISK=$(df -h /home | tail -1 | awk '{print $4" free of "$2}')

DB="${KAYAK_HOME}/DB/kayak.db"
if [[ -f "$DB" ]]; then
    DB_MTIME=$(stat -c %y "$DB" | cut -c1-19)
else
    DB_MTIME="(missing)"
fi

PL_LAST=$(systemctl show kayak-pipeline.service -p ExecMainStartTimestamp --value)
PL_STATUS=$(systemctl show kayak-pipeline.service -p ExecMainStatus --value)

SUBJECT="Kayak levels heartbeat — $HOST $(date +%Y-%m-%d)"

BODY=$(cat <<EOF
Kayak levels host heartbeat

Sent:        $TS
Host:        $HOST
Booted:      $BOOTED
Disk /home:  $DISK
DB mtime:    $DB_MTIME
Pipeline:    last=$PL_LAST status=$PL_STATUS

If you are seeing this, the msmtp -> Gmail alert path is still working.
EOF
)

echo "$BODY" | mail -s "$SUBJECT" pat.kayak@gmail.com
echo "$BODY" | logger -t kayak-heartbeat -p user.info
