#!/usr/bin/env bash
# Guard: no stale "PHPStan level 8" reference anywhere in the tree.
#
# The project is at level 9 (phpstan.neon). The README badge and a pre-commit
# comment drifted to "level 8" after the level-9 bump (#29) and escaped the
# round-3 cleanup because that sweep's grep was scoped to
# CLAUDE.md/.github/phpstan.neon — README was never checked. This makes the
# whole tree the gate so a hand-scoped sweep can't miss a surface again
# (review-4 R2.1).
#
# Excluded, with reason:
#   - docs/done/ , project-review-*/  : archived + in-flight review/plan docs
#     quote the historical "level 8" by nature (project-review-* is later
#     archived into docs/done/, as round-3's project-review-3/ was in #46).
#   - phpstan-baseline.neon           : the grandfathering baseline.
#   - phpstan.neon's "level 8->9" line: the one legitimate historical note.
#
# Matches the literal "level 8" / "level%208" (badge URL) forms only, so the
# hyphenated "level-8" historical prose in CHANGELOG stays untouched.
#
# Exit 0 if clean, 1 if a stale reference is found.
set -euo pipefail
cd "$(dirname "$0")/.."

hits=$(git grep -nIE 'level 8|level%208' -- \
        '*.md' '*.yaml' '*.yml' '*.neon' '*.php' 2>/dev/null \
    | grep -vE '^(docs/done/|project-review-)' \
    | grep -vE '^phpstan-baseline\.neon:' \
    | grep -vE '^phpstan\.neon:[0-9]+:.*8->9' \
    || true)

if [ -n "$hits" ]; then
    echo "ERR: stale 'PHPStan level 8' reference(s) — phpstan.neon is at level 9." >&2
    echo "Update them to the current level, or (if a legitimate historical note)" >&2
    echo "add an exclusion in scripts/check-phpstan-level.sh:" >&2
    echo "$hits" >&2
    exit 1
fi
echo "OK: no stale PHPStan-level references."
