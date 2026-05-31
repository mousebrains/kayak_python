#!/usr/bin/env bash
# Nightly metadata snapshot.
#
# Dumps the 15 metadata tables to data/db/*.csv via export_metadata.py and,
# if anything changed, commits and pushes to origin/main. Run by
# systemd timer kayak-metadata-snapshot.timer.
#
# Safety properties:
#   - Refuses to run unless the live tree is on `main` — this commits on the
#     checked-out branch and pushes origin/main, so a feature branch left
#     checked out here would strand the snapshot off-main (do branch work in a
#     worktree; see scripts/new-worktree.sh)
#   - Only stages data/db/*.csv (never scoops up other working-tree edits)
#   - Refuses to run if there are pre-existing staged changes outside data/db/
#   - Bails (non-zero exit -> OnFailure notify) if local main has diverged
#     from origin/main; no force-push, no auto-rebase

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

REPO="${KAYAK_HOME}/kayak"
VENV_PY="${KAYAK_HOME}/.venv/bin/python3"
LEVELS="${KAYAK_HOME}/.venv/bin/levels"
BRANCH=main

cd "$REPO"

# The live tree must be on $BRANCH: we commit on the checked-out branch and
# push origin/$BRANCH, so a feature branch left checked out here would commit
# the snapshot off-main and silently push nothing. Bail loudly (non-zero ->
# OnFailure notify) rather than write to the wrong place.
CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD || echo '(detached HEAD)')
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
    echo "Aborting: $REPO is on '$CURRENT_BRANCH', not '$BRANCH'." >&2
    echo "  Branch work belongs in a worktree (scripts/new-worktree.sh); the live tree stays on $BRANCH." >&2
    exit 1
fi

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

# Half-deploy guard (added after the 2026-05-31 wa.gov incident). The git pull
# above can bring new migration files live without anything having run
# `levels migrate` on this host, leaving the DB lagging its own committed
# yaml/CSV. export_metadata.py would then snapshot that mismatched DB and
# clobber main's CSVs (exactly the 04:33 failure). Refuse to snapshot a DB with
# pending migrations; the non-zero exit fires the OnFailure notify so a human
# runs `levels migrate` first.
if ! "$LEVELS" migrate --check; then
    echo "Aborting: the live DB has pending (unapplied) migrations — see above." >&2
    echo "  Run 'levels migrate' on this host NOW: the git pull already brought the" >&2
    echo "  new code live, so the hourly kayak-pipeline is running it against the old" >&2
    echo "  schema (orphan-check will red) until you migrate. Then the snapshot timer" >&2
    echo "  retries automatically." >&2
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
