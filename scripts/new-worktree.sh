#!/usr/bin/env bash
# Create (or attach) a git worktree for branch work, so it never touches the
# live editable tree at $KAYAK_HOME/kayak — which the venv imports from and the
# systemd jobs execute. See CLAUDE.md "Working on the live host".
#
# Usage: scripts/new-worktree.sh <branch-name>
#   - new branch:      forks <branch-name> off the latest origin/main
#   - existing branch: attaches a worktree tracking it
set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
REPO="${KAYAK_HOME}/kayak"
WT_ROOT="${KAYAK_HOME}/kayak-worktrees"

branch="${1:-}"
if [ -z "$branch" ]; then
    echo "usage: $(basename "$0") <branch-name>" >&2
    exit 2
fi

cd "$REPO"
git fetch --quiet origin main

dest="${WT_ROOT}/${branch}"
if git show-ref --quiet --verify "refs/heads/${branch}"; then
    git worktree add "$dest" "$branch"
else
    git worktree add "$dest" -b "$branch" origin/main
fi

echo "Worktree ready (branch '${branch}'):"
echo "  cd $dest"
