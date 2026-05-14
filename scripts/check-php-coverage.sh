#!/usr/bin/env bash
# Hard-fail if PHP line coverage in coverage.xml falls below FLOOR_PERCENT.
#
# Reads PHPUnit's Clover-format XML report (passed as argv 1 or
# PHP_COVERAGE_CLOVER env var; defaults to ./coverage.xml). The project
# rollup carries the totals on its first <metrics> element:
#
#   <metrics ... statements="N" coveredstatements="M" .../>
#
# Line coverage = coveredstatements / statements. Lower threshold
# (FLOOR_PERCENT, default 40) is the hard-fail floor; ratchet upward
# in follow-up PRs as coverage grows. T2.7 / PLAN_pre_release_followup.md.
#
# Exits 0 if coverage ≥ floor, 1 if below, 2 on parse error.

set -euo pipefail

CLOVER="${1:-${PHP_COVERAGE_CLOVER:-coverage.xml}}"
FLOOR_PERCENT="${FLOOR_PERCENT:-40}"

if [[ ! -r "$CLOVER" ]]; then
    echo "ERR: coverage report not readable at $CLOVER" >&2
    exit 2
fi

# Pull the project-level <metrics .../> line. Clover writes one per file
# plus a final rollup on the closing <project>. Grab the first <metrics>
# that sits directly under <project> (before the per-package <package>
# blocks) — that's the project-wide rollup PHPUnit emits.
metrics_line=$(grep -m1 -E '<project[^>]*>\s*<metrics' "$CLOVER" 2>/dev/null || true)
if [[ -z "$metrics_line" ]]; then
    # Fallback: some PHPUnit versions put the rollup at the end as a
    # bare <metrics> just before </project>.
    metrics_line=$(grep -oE '<metrics[^/]*statements="[0-9]+"[^/]*coveredstatements="[0-9]+"[^/]*/>' "$CLOVER" | tail -1)
fi
if [[ -z "$metrics_line" ]]; then
    echo "ERR: no <metrics .../> rollup found in $CLOVER" >&2
    exit 2
fi

statements=$(echo "$metrics_line" | grep -oE 'statements="[0-9]+"' | head -1 | grep -oE '[0-9]+')
covered=$(echo "$metrics_line" | grep -oE 'coveredstatements="[0-9]+"' | head -1 | grep -oE '[0-9]+')

if [[ -z "$statements" || -z "$covered" ]]; then
    echo "ERR: could not extract statements/coveredstatements from $CLOVER" >&2
    echo "  metrics line: $metrics_line" >&2
    exit 2
fi

if [[ "$statements" -eq 0 ]]; then
    echo "ERR: <metrics statements=\"0\"> — no executable statements found" >&2
    exit 2
fi

# Integer math at percent * 100 to avoid floating-point and to compare
# against an integer floor without bash needing bc.
pct_x100=$(( covered * 10000 / statements ))
pct_int=$(( pct_x100 / 100 ))
pct_frac=$(( pct_x100 % 100 ))
floor_x100=$(( FLOOR_PERCENT * 100 ))

printf 'PHP line coverage: %d/%d = %d.%02d%% (floor %d%%)\n' \
    "$covered" "$statements" "$pct_int" "$pct_frac" "$FLOOR_PERCENT"

if (( pct_x100 < floor_x100 )); then
    echo "FAIL: coverage below floor" >&2
    exit 1
fi
