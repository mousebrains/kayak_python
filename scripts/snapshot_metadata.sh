#!/usr/bin/env bash
# Nightly metadata snapshot.
#
# Dumps the 15 metadata tables to the kayak_data repo's *.csv via
# export_metadata.py and, if anything changed, commits and pushes to its
# origin/main. Run by systemd timer kayak-metadata-snapshot.timer.
#
# Since the data-repo split the snapshot targets the SEPARATE kayak_data clone
# ($KAYAK_DATA / METADATA_DIR), never the code repo — so it can't touch the
# branch-protected code-repo main (the round-6 lever). It pushes via the WRITE
# deploy key configured on the kayak_data clone (`git config core.sshCommand`,
# operator one-time setup — see deploy/SETUP.md).
#
# It reconciles editor-approved prod-direct metadata edits (review.php's
# `UPDATE reach …`) back into the CSVs so a rebuild / the next sync stays
# consistent with the live DB.
#
# Safety properties:
#   - Operates only inside $KAYAK_DATA; the code repo is untouched.
#   - Stages only the metadata CSVs; reaches*.json are dev-authored (re-traces),
#     so any byte-churn export produces for them is discarded.
#   - Refuses if kayak_data has pre-existing uncommitted changes.
#   - Refuses to snapshot a live DB with pending migrations (half-deploy guard).
#   - Bails if kayak_data's local main has diverged from origin (no force-push).

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

VENV_PY="${KAYAK_HOME}/.venv/bin/python3"
LEVELS="${KAYAK_HOME}/.venv/bin/levels"
KAYAK_DATA="${KAYAK_DATA:-${KAYAK_HOME}/kayak_data}"
EXPORT="${KAYAK_HOME}/kayak/scripts/export_metadata.py"
BRANCH=main

cd "$KAYAK_DATA"

# kayak_data is a dedicated metadata clone that always tracks main; a non-main
# checkout would commit/push the snapshot to the wrong place. Bail loudly
# (non-zero -> OnFailure notify) rather than write somewhere unexpected.
CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD || echo '(detached HEAD)')
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    echo "Aborting: $KAYAK_DATA is on '$CURRENT_BRANCH', not '$BRANCH'." >&2
    exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Aborting: $KAYAK_DATA has pre-existing uncommitted changes:" >&2
    git status --short >&2
    exit 1
fi

git fetch --quiet origin "$BRANCH"
LOCAL=$(git rev-parse @)
REMOTE=$(git rev-parse "@{u}")
BASE=$(git merge-base @ "@{u}")

if [ "$LOCAL" = "$REMOTE" ]; then
    :
elif [ "$LOCAL" = "$BASE" ]; then
    git pull --ff-only --quiet origin "$BRANCH"
elif [ "$REMOTE" = "$BASE" ]; then
    :
else
    echo "Aborting: $KAYAK_DATA local $BRANCH has diverged from origin/$BRANCH." >&2
    echo "Investigate with: git -C $KAYAK_DATA log --oneline --graph $BRANCH origin/$BRANCH" >&2
    exit 1
fi

# Half-deploy guard (the 2026-05-31 wa.gov incident). A code-repo deploy can
# bring new migration files live before anything has run `levels migrate` on this
# host, leaving the DB lagging its own schema. export_metadata.py would then
# snapshot that mismatched DB. Refuse to snapshot a DB with pending migrations;
# the non-zero exit fires the OnFailure notify so a human runs `levels migrate`.
if ! "$LEVELS" migrate --check; then
    echo "Aborting: the live DB has pending (unapplied) migrations — see above." >&2
    echo "  Run 'levels migrate' on this host NOW, then the snapshot timer retries." >&2
    exit 1
fi

"$VENV_PY" "$EXPORT" --out "$KAYAK_DATA" >/dev/null

# The snapshot owns the CSVs only; reaches*.json are dev-authored (re-traces, via
# a kayak_data PR), so discard any byte-churn export produced for them to keep
# the tree clean for the next run.
git checkout -- reaches.json reaches-gradient.json 2>/dev/null || true

if git diff --quiet -- '*.csv'; then
    echo "No metadata changes."
    exit 0
fi

git add -- '*.csv'

CHANGED=$(git diff --cached --name-only -- '*.csv' \
    | xargs -r -n1 basename | sed 's/\.csv$//' | paste -sd, -)

git commit --quiet -m "nightly metadata snapshot — ${CHANGED}"
git push --quiet origin "$BRANCH"

echo "Pushed snapshot: ${CHANGED}"
