#!/usr/bin/env bash
# Enforce the file-prefix convention for PHP file-private helpers.
#
# Rule: a `function _<name>(` definition inside `src/kayak/web/php/includes/<file>.php`
# must have `<name>` containing the file's basename stem (with the
# `_handler` and `_detail` suffixes stripped, so review_handler.php's
# stem is `review`). Two escape hatches:
#
#   1. The `_gp_*` cross-file cluster — gauge_plots.php /
#      gauge_plots_data.php / gauge_plots_filter.php deliberately share
#      these helpers. Allowlisted prefix.
#   2. `scripts/php-helper-prefix.allowlist` (one
#      `<repo-relative-path>:_<helper>` per line) — baseline for
#      grandfathered offenders. New helpers should follow the strict
#      rule; the allowlist is for not-blocking-the-T2.8 landing on
#      the existing surface. A rename PR is queued separately.
#
# Per CONVENTIONS.md "File-private helpers carry the file's prefix"
# and PLAN_pre_release_followup.md § T2.8. Wired as a local pre-commit
# hook (.pre-commit-config.yaml).
#
# Exit 0 = clean; 1 = violations (any helper not allowlisted and not
# matching the stem); 2 = script failure.

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

cd "$(dirname "$0")/.."

ALLOWLIST_FILE="scripts/php-helper-prefix.allowlist"
CROSS_FILE_PREFIX="_gp_"  # gauge_plots cluster

violations=()
allowlist_hits=0

# Read allowlist into a bash array (one "<path>:<helper>" entry per line).
# Comment lines and blank lines are ignored.
allowlist=()
if [[ -f "$ALLOWLIST_FILE" ]]; then
    while IFS= read -r entry; do
        [[ -z "$entry" || "$entry" =~ ^# ]] && continue
        allowlist+=("$entry")
    done < "$ALLOWLIST_FILE"
fi

in_allowlist() {
    local needle="$1"
    local item
    for item in "${allowlist[@]}"; do
        [[ "$item" == "$needle" ]] && return 0
    done
    return 1
}

while IFS= read -r -d '' file; do
    base=$(basename "$file" .php)
    stem="${base%_handler}"
    stem="${stem%_detail}"

    while IFS= read -r line; do
        # `function _foo(` and `function _foo (` both legal
        if ! [[ "$line" =~ ^function[[:space:]]+(_[a-zA-Z_][a-zA-Z_0-9]*)[[:space:]]*\( ]]; then
            continue
        fi
        helper="${BASH_REMATCH[1]}"

        # Cross-file cluster exception
        if [[ "$helper" == "${CROSS_FILE_PREFIX}"* ]]; then
            continue
        fi

        # Convention check: helper name (sans leading underscore) must
        # contain the file's basename stem
        if [[ "${helper#_}" == *"${stem}"* ]]; then
            continue
        fi

        # Grandfathered allowlist
        if in_allowlist "${file}:${helper}"; then
            allowlist_hits=$((allowlist_hits + 1))
            continue
        fi

        violations+=("${file}: '${helper}' does not contain stem '${stem}'")
    done < <(grep -E '^function[[:space:]]+_[a-zA-Z_]+' "$file" 2>/dev/null || true)
done < <(find src/kayak/web/php/includes -name '*.php' -type f -print0 2>/dev/null)

if (( ${#violations[@]} > 0 )); then
    printf 'php helper-prefix violations:\n' >&2
    printf '  %s\n' "${violations[@]}" >&2
    printf '\n' >&2
    printf '%d violation(s) total. Either rename to start with the file stem,\n' "${#violations[@]}" >&2
    printf 'or add a "<path>:<_helper>" entry to %s (grandfathered only).\n' "$ALLOWLIST_FILE" >&2
    exit 1
fi

if (( allowlist_hits > 0 )); then
    printf '%d grandfathered helper(s) from %s — see CONVENTIONS.md § T2.8.\n' \
        "$allowlist_hits" "$ALLOWLIST_FILE"
fi
