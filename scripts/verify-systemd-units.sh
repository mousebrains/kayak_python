#!/usr/bin/env bash
# Validate systemd unit syntax with `systemd-analyze verify` (review-4 R4.2).
#
# Catches malformed OnCalendar=/ExecStart=, unknown directives, etc. before they
# reach prod. `systemd-analyze` is Linux-only, so this runs in CI rather than the
# macOS dev gate. The units are written for the PROD layout (/home/pat ExecStart,
# EnvironmentFile, ReadWritePaths; /etc/kayak), which doesn't exist on a stock
# runner -- so `verify` emits "not found"/"not executable" problems and exits
# non-zero. That's expected: we drop lines that reference a known-absent prod
# path and fail only on problems that DON'T (i.e. genuine syntax/directive
# errors). Pass the unit files as arguments.
set -uo pipefail

[ "$#" -ge 1 ] || { echo "usage: $0 UNIT..." >&2; exit 2; }

raw=$(systemd-analyze verify "$@" 2>&1 || true)
[ -n "$raw" ] && printf '%s\n' "$raw"

# Tolerate problems explained solely by the prod install paths being absent here.
genuine=$(printf '%s\n' "$raw" | grep -vE '/home/pat|/etc/kayak' | grep -vE '^[[:space:]]*$' || true)
if [ -n "$genuine" ]; then
    echo "ERROR: systemd-analyze reported problems not explained by missing prod paths:" >&2
    printf '%s\n' "$genuine" >&2
    exit 1
fi
echo "systemd unit validation: OK (prod-path warnings tolerated)"
exit 0
