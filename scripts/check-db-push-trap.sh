#!/usr/bin/env bash
# Guard (round-5 R1.2): db_push.sh must restart the pipeline/backup timers on ANY
# failure between the timer-stop and the restart — i.e. a `trap restart_timers
# EXIT` must be armed. The round-4 review found this missing; the round-4 plan
# recorded it "shipped" but it never landed (round 5). The fix is only durable if
# CI enforces it, so this guard runs in lint-misc. See project-review-5/.
#
# Also a non-vacuity belt for R1.1: the `DELETE FROM pages` restore-breaker (the
# table was dropped 58 migrations ago; under set -e it aborted the restore before
# the swap + restart) must stay gone.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

# `^[[:space:]]*` (POSIX, not the GNU-only `\s`) so the check is grep-flavor-safe;
# the anchor ignores a commented-out `# trap …` line.
if ! grep -qE '^[[:space:]]*trap restart_timers EXIT' scripts/db_push.sh; then
    echo "ERR: scripts/db_push.sh is missing 'trap restart_timers EXIT'." >&2
    echo "     A failure between the timer-stop and the restart would strand the" >&2
    echo "     pipeline + backup timers stopped on prod (round-5 R1.2)." >&2
    fail=1
fi

if grep -q 'DELETE FROM pages' scripts/db_push.sh; then
    echo "ERR: 'DELETE FROM pages' is back in scripts/db_push.sh — that table was" >&2
    echo "     dropped by 0006 and aborts the restore under set -e (round-5 R1.1)." >&2
    fail=1
fi

if [ "$fail" -ne 0 ]; then
    exit 1
fi
echo "OK: db_push.sh has the restart trap and no DELETE FROM pages."
