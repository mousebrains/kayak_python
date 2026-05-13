#!/usr/bin/env bash
# Nightly metadata snapshot.
#
# Dumps the 15 metadata tables to data/db/*.csv via export_metadata.py and,
# if anything changed, commits and pushes to origin/main. Run by
# systemd timer kayak-metadata-snapshot.timer.
#
# Safety properties:
#   - Only stages data/db/*.csv (never scoops up other working-tree edits)
#   - Refuses to run if there are pre-existing staged changes outside data/db/
#   - Bails (non-zero exit -> OnFailure notify) if local main has diverged
#     from origin/main; no force-push, no auto-rebase

set -euo pipefail

REPO=/home/pat/kayak
VENV_PY=/home/pat/.venv/bin/python3
BRANCH=main

cd "$REPO"

OUTSIDE_STAGED=$(git diff --cached --name-only -- ':!data/db/')
if [ -n "$OUTSIDE_STAGED" ]; then
    echo "Aborting: staged changes exist outside data/db/:" >&2
    printf '  %s\n' $OUTSIDE_STAGED >&2
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
    echo "Aborting: local $BRANCH has diverged from origin/$BRANCH." >&2
    echo "Investigate with: git log --oneline --graph $BRANCH origin/$BRANCH" >&2
    exit 1
fi

"$VENV_PY" "$REPO/scripts/export_metadata.py" --out "$REPO/data/db" >/dev/null

if git diff --quiet -- 'data/db/*.csv'; then
    echo "No metadata changes."
    exit 0
fi

git add -- 'data/db/*.csv'

CHANGED=$(git diff --cached --name-only -- 'data/db/*.csv' \
    | xargs -r -n1 basename | sed 's/\.csv$//' | paste -sd, -)

git commit --quiet -m "data/db: nightly metadata snapshot — ${CHANGED}"
git push --quiet origin "$BRANCH"

echo "Pushed snapshot: ${CHANGED}"
