#!/usr/bin/env bash
# Idempotent code-deploy for /home/pat/kayak on the live host.
#
# Pulls main, refreshes Python deps (only if pyproject.toml changed),
# applies any pending SQL migrations, regenerates the static HTML to
# OUTPUT_DIR. Does NOT touch /etc/systemd/system/, /etc/nginx/, or any
# other root-owned config — those rare structural changes need the
# diff-then-cp manual flow per feedback_sudo_cp_clobbers_overrides /
# feedback_systemd_in_tree_copy. If a deploy lands systemd or nginx
# changes, this script prints a NOTICE so the operator knows to apply
# them by hand.
#
# Phase 3.1 of docs/PLAN_production_discipline.md. Manual today; the
# eventual CI-driven invocation (Phase 3.2) will run this same script
# under a deploy-only system user.
#
# Exits 0 on success. Exits non-zero on any sub-step failure thanks to
# set -e; the caller (operator shell or future GHA) can treat that as
# the failure signal.

set -euo pipefail

# Path indirection — sourced /etc/kayak/env overrides; otherwise the
# default keeps the dev shell working without the file present.
: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

REPO="${KAYAK_HOME}/kayak"
VENV_PIP="${KAYAK_HOME}/.venv/bin/pip"
VENV_PY="${KAYAK_HOME}/.venv/bin/python"
LEVELS="${KAYAK_HOME}/.venv/bin/levels"

# The metadata snapshot (CSVs + reaches*.json) lives in a separate repo cloned
# alongside the code repo (data-repo split). levels/import_metadata read it via
# METADATA_DIR; export it here so they find it regardless of ~/.config/kayak/.env.
KAYAK_DATA="${KAYAK_DATA:-${KAYAK_HOME}/kayak_data}"
export METADATA_DIR="$KAYAK_DATA"

# --- preconditions -----------------------------------------------------

if [[ "$(id -un)" != "pat" ]]; then
    echo "ERR: deploy.sh must run as user 'pat' (got '$(id -un)')" >&2
    exit 1
fi

if [[ "$(pwd)" != "$REPO" ]]; then
    echo "ERR: deploy.sh must run from $REPO (got '$(pwd)')" >&2
    exit 1
fi

if [[ ! -x "$LEVELS" ]]; then
    echo "ERR: levels CLI not found at $LEVELS" >&2
    exit 1
fi

# Refuse to deploy with uncommitted changes — a `git pull --ff-only`
# would still succeed, but the tree state would be ambiguous and
# rollback (re-deploy at a previous SHA) becomes lossy.
if ! git diff-index --quiet HEAD --; then
    echo "ERR: working tree has uncommitted changes; commit or stash first" >&2
    git status --short >&2
    exit 1
fi

# Refuse unless on main — deploying a feature branch is the live-tree footgun
# scripts/snapshot_metadata.sh guards against; deploy.sh must guard it too.
branch="$(git symbolic-ref --short HEAD 2>/dev/null || echo detached)"
if [[ "$branch" != "main" ]]; then
    echo "ERR: deploy.sh must run on 'main' (got '$branch')" >&2
    exit 1
fi

# --- record pre-pull state for change detection -----------------------

old_sha=$(git rev-parse HEAD)

# --- 1. pull main ------------------------------------------------------

echo ">>> git pull --ff-only"
git pull --ff-only

new_sha=$(git rev-parse HEAD)

if [[ "$old_sha" == "$new_sha" ]]; then
    echo "(already at $new_sha — no new commits)"
else
    echo "(advanced from $old_sha to $new_sha)"
fi

# --- 2. python deps (only if pyproject.toml changed) ------------------

if [[ "$old_sha" != "$new_sha" ]] && \
        ! git diff --quiet "$old_sha" "$new_sha" -- pyproject.toml; then
    echo ">>> pyproject.toml changed — refreshing venv via pip install -e ."
    "$VENV_PIP" install -e .
else
    echo "(pyproject.toml unchanged — skipping pip install)"
fi

# --- 2.5. validate config ---------------------------------------------
#
# Pydantic surfaces any invalid env (out-of-range int, malformed URL,
# bad email, extra kwargs) BEFORE migrate so a misconfig can't half-
# apply schema changes. Runs after `pip install -e .` so the latest
# model is loaded. Exit 1 = field invalid; exit 2 = runner failure;
# either fails the deploy.

echo ">>> levels validate-config"
"$LEVELS" validate-config

# --- 3. migrate --------------------------------------------------------

echo ">>> levels migrate"
"$LEVELS" migrate

# --- 3.05. pull the metadata repo (kayak_data) -------------------------
#
# The metadata snapshot (CSVs + reaches*.json) lives in a separate repo
# (data-repo split); steps 3.1/3.25/3.26 apply whatever it now holds, read via
# METADATA_DIR. Pull it here and record its old/new SHA so those steps can gate
# on the metadata actually changing (the code-repo SHA no longer moves when only
# metadata changes). --ff-only mirrors the code-repo pull: no merge, no rebase.

# Pre-flight: a fresh/unprovisioned host has no clone yet — fail with an
# actionable message instead of a raw `fatal: … HEAD` / publickey abort.
if [[ ! -e "$KAYAK_DATA/.git" ]] || ! git -C "$KAYAK_DATA" rev-parse HEAD >/dev/null 2>&1; then
    echo "ERR: kayak_data clone missing or empty at $KAYAK_DATA" >&2
    echo "  Complete the metadata-repo setup first (deploy/SETUP.md § 2.5):" >&2
    echo "    git clone git@github.com:mousebrains/kayak_data.git $KAYAK_DATA" >&2
    exit 1
fi

echo ">>> git -C $KAYAK_DATA pull --ff-only"
data_old_sha=$(git -C "$KAYAK_DATA" rev-parse HEAD)
git -C "$KAYAK_DATA" pull --ff-only
data_new_sha=$(git -C "$KAYAK_DATA" rev-parse HEAD)

# --- 3.1. sync metadata CSVs (only if kayak_data's *.csv changed) ------
#
# Apply the reviewed CSV diff to the live DB by stable id: INSERT new, UPDATE
# changed (a rename is an UPDATE — matched by id, so the source's observations
# are preserved). --backup snapshots the DB to <db>.pre-sync first: a FK-valid
# but logically-wrong CSV edit (e.g. a bad threshold UPDATE) commits and is NOT
# undoable from the one-line diff. Runs WITHOUT --allow-deletes: a diff that
# REMOVES a row prints the per-source observation-drop plan and exits non-zero,
# so this `set -e`d deploy ABORTS — but note the safe insert/update half is
# already COMMITTED at that point. The operator reviews the drop counts, runs
# `levels sync-metadata --allow-deletes` by hand (the committed upserts are
# idempotent, so re-running deploy.sh is safe), then re-runs deploy.sh. Runs
# after migrate (schema current) and before the geom/gradient applies (which
# UPDATE the same reach rows). Gated on the metadata CSVs actually changing.

if [[ "$data_old_sha" != "$data_new_sha" ]] && \
        ! git -C "$KAYAK_DATA" diff --quiet "$data_old_sha" "$data_new_sha" -- '*.csv'; then
    echo ">>> metadata CSVs changed — applying metadata sync (levels sync-metadata --backup)"
    "$LEVELS" sync-metadata --backup
else
    echo "(metadata CSVs unchanged — skipping metadata sync)"
fi

# --- 3.25. apply reach geometry (only if reaches.json changed) --------
#
# reach.geom lives in kayak_data's reaches.json (excluded from reach.csv —
# large, and not regenerable on prod without the dev-only DEM/NHD trace
# stack). It is NOT migration-managed, so a dev re-trace reaches prod
# only by re-running this snapshot apply. --geom-only skips the CSV
# upsert and just runs `UPDATE reach SET geom` from METADATA_DIR's JSON.
# Runs after migrate (reach table is current) and is gated on the file
# actually changing between the kayak_data SHAs so an unchanged deploy
# does no DB writes.

if [[ "$data_old_sha" != "$data_new_sha" ]] && \
        ! git -C "$KAYAK_DATA" diff --quiet "$data_old_sha" "$data_new_sha" -- reaches.json; then
    echo ">>> reaches.json changed — applying geom (import_metadata.py --geom-only)"
    "$VENV_PY" scripts/import_metadata.py --geom-only
else
    echo "(reaches.json unchanged — skipping geom apply)"
fi

# --- 3.26. apply reach gradient (only if reaches-gradient.json changed) -
#
# reach.gradient_profile lives in kayak_data's reaches-gradient.json (excluded
# from reach.csv — large, not regenerable on prod), the same snapshot pattern as
# geom above. review-3 R6.1.

if [[ "$data_old_sha" != "$data_new_sha" ]] && \
        ! git -C "$KAYAK_DATA" diff --quiet "$data_old_sha" "$data_new_sha" -- reaches-gradient.json; then
    echo ">>> reaches-gradient.json changed — applying gradient (import_metadata.py --gradient-only)"
    "$VENV_PY" scripts/import_metadata.py --gradient-only
else
    echo "(reaches-gradient.json unchanged — skipping gradient apply)"
fi

# --- 3.5. emit /etc/kayak/runtime-config.json -------------------------
#
# Writes the typed-config JSON snapshot consumed by PHP. emit-config renders
# the JSON UNPRIVILEGED (as pat, to stdout via --dry-run); the root-owned
# /usr/local/sbin/kayak-install-runtime-config wrapper validates + atomically
# installs it (0640 root:www-data). The sudoers grant runs only that fixed
# wrapper, never the pat-writable venv binary (review-3 R1.5). Requires the
# deploy/sudoers.d/kayak-emit-config grant + the wrapper installed (one-time
# operator setup; see deploy/SETUP.md). No php-fpm reload needed — PHP re-reads
# the JSON once per request. (The wrapper always re-installs; harmless — a few
# KiB, re-read per request, no mtime watcher.)

echo ">>> emit-config (unprivileged) | install-runtime-config (root wrapper)"
"$LEVELS" emit-config --dry-run | sudo -n /usr/local/sbin/kayak-install-runtime-config

# --- 4. build static HTML ---------------------------------------------

echo ">>> levels build"
"$LEVELS" build

# --- 5. flag config drift that this script intentionally won't apply --

if [[ "$old_sha" != "$new_sha" ]]; then
    changed_paths=$(git diff --name-only "$old_sha" "$new_sha" -- systemd/ conf/sites/ conf/snippets/ deploy/ 2>/dev/null || true)
    if [[ -n "$changed_paths" ]]; then
        echo
        echo "NOTICE: this deploy touched systemd/nginx/config files:"
        echo "$changed_paths" | sed 's/^/  /'
        echo "Apply by hand after diffing against /etc/. See"
        echo "  feedback_sudo_cp_clobbers_overrides (CLAUDE memory) and"
        echo "  deploy/SETUP.md for the canonical install paths."
    fi
fi

echo
echo "Deploy complete at $new_sha"
