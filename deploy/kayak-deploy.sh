#!/usr/bin/env bash
# kayak-deploy — paired-release activation orchestrator (S7, decision D2).
#
# One deployment = one immutable engine commit + one immutable dataset commit
# + this host's configuration, staged together as a release under
# $KAYAK_DEPLOY_ROOT/releases/<release-id>/ and activated by an atomic
# symlink switch of $KAYAK_DEPLOY_ROOT/current. The release id derives from
# the wheel digest, dataset commit, and host-config fingerprint, so a
# host-config-only change is a distinct release (plan §S7).
#
# Per decision D2 (2026-06-12) this deployer verifies SHA-256 digests and
# protected-branch reachability instead of manifest signatures, and is
# versioned with a documentation note instead of min-version negotiation:
# both repos are branch-protected with required CI, so a full commit SHA
# reachable from the protected branch IS the trust anchor.
#
# Usage:
#   kayak-deploy --engine-ref <40-hex> --dataset-ref <40-hex> [--stage-only]
#
# Configuration: /etc/kayak/deploy.env (override: $KAYAK_DEPLOY_CONF), vars:
#   ENGINE_REPO / DATASET_REPO     git URLs (or local paths) of the two repos
#   ENGINE_BRANCH / DATASET_BRANCH protected branch each ref must be reachable
#                                  from (default: main)
#   KAYAK_UNITS                    space-separated units to stop/start around
#                                  activation (default: the kayak-* timers)
#   HEALTH_URL                     URL curl'd after activation (optional)
# Path overrides (mainly for tests / the clean-VM rehearsal):
#   KAYAK_DEPLOY_ROOT              release root (default /opt/kayak)
#
# Requires on the host: git, sqlite3, curl, and a python3 whose stdlib
# venv/ensurepip work (Debian: apt install python3-venv) — no system pip
# needed; scratch and release venvs bootstrap their own.
#
# Phases:
#   validate  ref shape + protected-branch reachability (no mutation)
#   stage     build wheel -> venv, snapshot dataset, validate contract,
#             emit non-secret runtime config, write release.json (no
#             system mutation outside the new release dir)
#   activate  maintenance on -> quiesce consumers (timers AND services) ->
#             DB backup -> migrate -> all-or-nothing sync -> geom/gradient
#             sidecars -> build docroot -> atomic symlink ->
#             health check -> start consumers -> maintenance off.
#             Any failure rolls back to the previous release + DB backup.
#
# --stage-only ends after `stage` and prints the release path: this is the
# test/rehearsal mode, and the recommended first run on any new host.
#
# Deployer version (D2: documented, not negotiated). Bump on incompatible
# release-layout changes and note the migration in deploy/SETUP.md.
KAYAK_DEPLOY_VERSION=1

set -euo pipefail

# ---------------------------------------------------------------------------
# Arguments + configuration
# ---------------------------------------------------------------------------
ENGINE_REF=""
DATASET_REF=""
STAGE_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --engine-ref)  ENGINE_REF="${2:?--engine-ref needs a value}"; shift 2 ;;
        --dataset-ref) DATASET_REF="${2:?--dataset-ref needs a value}"; shift 2 ;;
        --stage-only)  STAGE_ONLY=1; shift ;;
        -h|--help)
            sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Error: unknown argument '$1' (see --help)" >&2; exit 2 ;;
    esac
done

CONF="${KAYAK_DEPLOY_CONF:-/etc/kayak/deploy.env}"
if [ -r "$CONF" ]; then
    # shellcheck source=/dev/null
    . "$CONF"
fi
# The standard host environment (SITE_URL, SQLITE_PATH, …) — the staged
# runtime config and the activation steps must see the same env every other
# kayak job runs with.
if [ -r /etc/kayak/env ]; then
    # shellcheck source=/dev/null
    . /etc/kayak/env
fi
: "${ENGINE_REPO:?ENGINE_REPO must be set in $CONF (git URL/path of kayak_python)}"
: "${DATASET_REPO:?DATASET_REPO must be set in $CONF (git URL/path of kayak_data)}"
: "${ENGINE_BRANCH:=main}"
: "${DATASET_BRANCH:=main}"
: "${KAYAK_UNITS:=kayak-pipeline.timer kayak-backup-hourly.timer kayak-backup-weekly.timer kayak-decimate.timer kayak-status.timer kayak-fetch-osmb.timer kayak-editor-retention.timer kayak-audit-gauges.timer}"
: "${HEALTH_URL:=}"
ROOT="${KAYAK_DEPLOY_ROOT:-/opt/kayak}"

# Full 40-hex commit SHAs only — never a branch or tag (plan §S7; and the
# pin-gate incident: short/hand-typed refs are exactly how SHAs go wrong).
case "$ENGINE_REF" in
    *[!0-9a-f]*|"") echo "Error: --engine-ref must be a full 40-hex commit SHA" >&2; exit 2 ;;
esac
case "$DATASET_REF" in
    *[!0-9a-f]*|"") echo "Error: --dataset-ref must be a full 40-hex commit SHA" >&2; exit 2 ;;
esac
if [ "${#ENGINE_REF}" -ne 40 ] || [ "${#DATASET_REF}" -ne 40 ]; then
    echo "Error: refs must be full 40-hex commit SHAs (got ${#ENGINE_REF}/${#DATASET_REF} chars)" >&2
    exit 2
fi

SCRATCH="$(mktemp -d)"
CLEAN_SCRATCH=1
cleanup() {
    [ "$CLEAN_SCRATCH" = 1 ] && rm -rf "$SCRATCH"
}
trap cleanup EXIT

sha256() { # portable: sha256sum (Linux) or shasum -a 256 (macOS)
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

log() { echo "kayak-deploy: $*"; }

# Python for venv creation + wheel building. Only the stdlib `venv` module
# (with its bundled ensurepip) is required — the host python needs NO pip of
# its own: a scratch venv supplies pip for the wheel build, and the release
# venv bootstraps its own. Override with KAYAK_DEPLOY_PYTHON if the default
# python3 lacks ensurepip (some minimal/venv-of-venv environments).
PYTHON="${KAYAK_DEPLOY_PYTHON:-python3}"
if ! "$PYTHON" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    echo "Error: $PYTHON lacks the stdlib venv/ensurepip modules; install python3-venv or set KAYAK_DEPLOY_PYTHON" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Phase 1 — validate: each ref must be reachable from its protected branch
# ---------------------------------------------------------------------------
fetch_and_verify() { # <repo> <ref> <branch> <clone-dir>
    repo="$1"; ref="$2"; branch="$3"; dir="$4"
    log "fetching $repo (branch $branch)"
    git clone --quiet --bare --branch "$branch" --single-branch "$repo" "$dir"
    # The ref itself may be older than the branch tip; fetch it explicitly
    # (works for any reachable commit on a bare clone of the branch).
    if ! git -C "$dir" cat-file -e "$ref^{commit}" 2>/dev/null; then
        echo "Error: $ref not found in $repo" >&2
        exit 1
    fi
    if ! git -C "$dir" merge-base --is-ancestor "$ref" "$branch"; then
        echo "Error: $ref is not reachable from protected branch '$branch' of $repo" >&2
        exit 1
    fi
}

fetch_and_verify "$ENGINE_REPO" "$ENGINE_REF" "$ENGINE_BRANCH" "$SCRATCH/engine.git"
fetch_and_verify "$DATASET_REPO" "$DATASET_REF" "$DATASET_BRANCH" "$SCRATCH/dataset.git"
log "refs verified against protected branches"

# ---------------------------------------------------------------------------
# Phase 2 — stage the release (no system mutation outside the release dir)
# ---------------------------------------------------------------------------
log "building engine wheel at $ENGINE_REF"
git clone --quiet --no-checkout "$SCRATCH/engine.git" "$SCRATCH/engine-src"
git -C "$SCRATCH/engine-src" checkout --quiet "$ENGINE_REF"
# Dependency + build-backend locks (PR #190 review): the engine commit
# carries requirements-prod.lock (runtime deps, exported from uv.lock,
# drift-checked in CI) and requirements-build.lock (the PEP 517 backend) —
# both hash-pinned, so neither runtime deps nor the code that BUILDS the
# wheel can drift from the reviewed engine SHA. Both digests participate in
# the release identity, and the wheel builds with --no-build-isolation from
# the preinstalled locked backend (no network resolution at build time).
REQ_LOCK="$SCRATCH/engine-src/requirements-prod.lock"
BUILD_LOCK="$SCRATCH/engine-src/requirements-build.lock"
for lock in "$REQ_LOCK" "$BUILD_LOCK"; do
    if [ ! -f "$lock" ]; then
        echo "Error: engine commit lacks $(basename "$lock") (pre-Batch-4B engine?)" >&2
        exit 1
    fi
done
LOCK_SHA="$(sha256 "$REQ_LOCK")"
BUILD_LOCK_SHA="$(sha256 "$BUILD_LOCK")"

"$PYTHON" -m venv "$SCRATCH/buildenv"
"$SCRATCH/buildenv/bin/pip" install --quiet --require-hashes -r "$BUILD_LOCK"
# Reproducible wheel bytes: zip entries otherwise carry checkout mtimes, so
# the same engine SHA would hash differently per clone and break the
# same-inputs => same-release-id property. hatchling honors
# SOURCE_DATE_EPOCH; pin it to the commit's own timestamp.
SOURCE_DATE_EPOCH="$(git -C "$SCRATCH/engine-src" log -1 --format=%ct "$ENGINE_REF")"
export SOURCE_DATE_EPOCH
"$SCRATCH/buildenv/bin/pip" wheel --quiet --no-deps --no-build-isolation \
    -w "$SCRATCH/dist" "$SCRATCH/engine-src"
WHEEL="$(ls "$SCRATCH"/dist/*.whl)"
WHEEL_SHA="$(sha256 "$WHEEL")"

log "snapshotting dataset at $DATASET_REF"
git -C "$SCRATCH/dataset.git" archive --format=tar -o "$SCRATCH/dataset.tar" "$DATASET_REF"
DATASET_TAR_SHA="$(sha256 "$SCRATCH/dataset.tar")"
mkdir -p "$SCRATCH/dataset"
tar -xf "$SCRATCH/dataset.tar" -C "$SCRATCH/dataset"

# Staging toolchain: the engine under deploy, in the scratch venv (locked
# deps + the wheel), used for validation and config emission BEFORE any
# release dir exists.
log "installing staged engine into the scratch toolchain (hash-locked deps)"
"$SCRATCH/buildenv/bin/pip" install --quiet --require-hashes -r "$REQ_LOCK"
"$SCRATCH/buildenv/bin/pip" install --quiet --no-deps "$WHEEL"
STAGE_LEVELS="$SCRATCH/buildenv/bin/levels"

log "validating dataset contract with the staged engine"
"$STAGE_LEVELS" validate-dataset "$SCRATCH/dataset"

# Runtime config, emitted from THIS host environment with the staged engine
# + dataset. Its digest participates in the release identity (PR #190: a
# /etc/kayak/env change must mint a NEW release) — but computed over a
# NORMALIZED view: staging-local path fields are excluded so identical
# inputs give an identical release id (the raw emit necessarily contains
# scratch paths at this point), and operational tokens (ntfy/healthcheck
# URLs) are excluded because the release-retained copy must not widen their
# lifetime/ownership boundary. The canonical secret-merged config still
# lives ONLY at /etc/kayak/runtime-config.json via the root wrapper.
log "emitting runtime config (normalized digest)"
DATASET_DIR="$SCRATCH/dataset" OUTPUT_DIR="$SCRATCH/dataset" \
    "$STAGE_LEVELS" emit-config --out "$SCRATCH/runtime-config-raw.json"
normalize_config() { # <in> <out>: drop path-local + token fields, sort keys
    "$PYTHON" - "$1" "$2" <<'PYNORM'
import json
import sys

drop = {"dataset_dir", "output_dir", "osmb_dir", "map_layers_dir",
        "gauge_metadata_cache", "database_path", "ntfy_topic"}
data = json.load(open(sys.argv[1]))
data = {k: v for k, v in data.items() if k not in drop and not k.startswith("hc_")}
json.dump(data, open(sys.argv[2], "w"), indent=2, sort_keys=True)
PYNORM
}
normalize_config "$SCRATCH/runtime-config-raw.json" "$SCRATCH/runtime-config.json"
CONFIG_SHA="$(sha256 "$SCRATCH/runtime-config.json")"

# Host-config fingerprint: the non-secret host shape participates in the
# release identity so a host-config-only change is a new release.
HOST_YAML="${KAYAK_HOST_CONFIG:-/etc/kayak/host.yaml}"
if [ -r "$HOST_YAML" ]; then
    HOST_FP="$(sha256 "$HOST_YAML")"
else
    HOST_FP="none"
fi

RELEASE_ID="$(printf '%s %s %s %s %s %s' "$WHEEL_SHA" "$DATASET_REF" "$HOST_FP" "$CONFIG_SHA" "$LOCK_SHA" "$BUILD_LOCK_SHA" \
    | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } \
    | cut -c1-12)"
RELEASE_DIR="$ROOT/releases/$RELEASE_ID"

if [ -e "$RELEASE_DIR/release.json" ]; then
    log "release $RELEASE_ID already staged at $RELEASE_DIR"
else
    log "staging release $RELEASE_ID"
    mkdir -p "$RELEASE_DIR/dataset" "$RELEASE_DIR/docroot"
    tar -xf "$SCRATCH/dataset.tar" -C "$RELEASE_DIR/dataset"
    "$PYTHON" -m venv "$RELEASE_DIR/venv"
    "$RELEASE_DIR/venv/bin/pip" install --quiet --require-hashes -r "$REQ_LOCK"
    "$RELEASE_DIR/venv/bin/pip" install --quiet --no-deps "$WHEEL"
    cp "$WHEEL" "$REQ_LOCK" "$SCRATCH/runtime-config.json" "$RELEASE_DIR/"

    # release.json — the digest-verified release manifest (D2).
    cat > "$RELEASE_DIR/release.json" <<EOF
{
  "release_id": "$RELEASE_ID",
  "deployer_version": $KAYAK_DEPLOY_VERSION,
  "engine_sha": "$ENGINE_REF",
  "dataset_sha": "$DATASET_REF",
  "wheel": "$(basename "$WHEEL")",
  "wheel_sha256": "$WHEEL_SHA",
  "dataset_tar_sha256": "$DATASET_TAR_SHA",
  "requirements_lock_sha256": "$LOCK_SHA",
  "build_lock_sha256": "$BUILD_LOCK_SHA",
  "runtime_config_sha256": "$CONFIG_SHA",
  "host_config_fingerprint": "$HOST_FP",
  "staged_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
fi

# Verify the staged wheel digest before any activation step touches the
# system: the release dir may predate this invocation.
STAGED_WHEEL="$RELEASE_DIR/$(basename "$WHEEL")"
if [ "$(sha256 "$STAGED_WHEEL")" != "$WHEEL_SHA" ]; then
    echo "Error: staged wheel digest mismatch in $RELEASE_DIR — refusing to activate" >&2
    exit 1
fi

log "staged: $RELEASE_DIR"
if [ "$STAGE_ONLY" = 1 ]; then
    log "stage-only requested — stopping before activation"
    echo "$RELEASE_DIR"
    exit 0
fi

# Activation gate (PR #190 review P1): switching $ROOT/current is only
# meaningful once the host's SERVING config (nginx root, FPM open_basedir/
# KAYAK_CONFIG_PATH) points at it — that re-pointing is the Batch 4C
# cutover, recorded by the installer in deploy.env. Refusing here keeps
# this deployer from reporting success while users are still served the
# legacy docroot.
if [ "${SERVING_CUTOVER:-no}" != "yes" ]; then
    echo "Error: this host's serving config has not been cut over to $ROOT/current" >&2
    echo "(SERVING_CUTOVER=yes not set in $CONF — done by the Batch 4C install/migration" >&2
    echo "runbook). Use --stage-only, or deploy with scripts/deploy.sh until then." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Phase 3 — activate (system mutation; everything before the symlink switch
# is undone by the rollback path)
# ---------------------------------------------------------------------------
PREV_TARGET=""
if [ -L "$ROOT/current" ]; then
    PREV_TARGET="$(readlink "$ROOT/current")"
fi

DB_PATH="${SQLITE_PATH:-}"
if [ -z "$DB_PATH" ] && [ -r /etc/kayak/env ]; then
    DB_PATH="$(. /etc/kayak/env >/dev/null 2>&1; echo "${SQLITE_PATH:-}")"
fi
: "${DB_PATH:?SQLITE_PATH must be set (env or /etc/kayak/env) for activation}"

PRE_BACKUP="$SCRATCH/pre-activate.db"
MUTATED=0

SWITCHED=0
rollback() {
    status=$?
    trap - ERR
    echo "kayak-deploy: FAILURE (exit $status) — rolling back" >&2
    if [ "$MUTATED" = 1 ] && [ -f "$PRE_BACKUP" ]; then
        echo "kayak-deploy: restoring pre-activation DB backup" >&2
        sqlite3 "$DB_PATH" ".restore '$PRE_BACKUP'" || \
            echo "kayak-deploy: DB RESTORE FAILED — manual recovery from $PRE_BACKUP required" >&2
        CLEAN_SCRATCH=0
    fi
    if [ -z "$PREV_TARGET" ]; then
        # Virgin host: there is no previous release to fall back to, so ANY
        # activation failure — before or after the symlink switch — leaves
        # the host in maintenance mode with consumers stopped; a half-
        # activated first install must not serve or write (PR #190 review
        # P2, both rounds). Remove 'current' only if the switch happened.
        [ "$SWITCHED" = 1 ] && rm -f "$ROOT/current"
        echo "kayak-deploy: first activation failed — host left in maintenance mode" >&2
        echo "kayak-deploy: with consumers stopped (no prior release to fall back to)" >&2
        exit "$status"
    fi
    if [ -n "$PREV_TARGET" ]; then
        ln -s "$PREV_TARGET" "$ROOT/current.new" && mv -f "$ROOT/current.new" "$ROOT/current"
        echo "kayak-deploy: current -> $PREV_TARGET (previous release)" >&2
    fi
    for u in $KAYAK_UNITS; do systemctl start "$u" 2>/dev/null || true; done
    rm -f "$ROOT/maintenance"
    exit "$status"
}
trap rollback ERR

log "entering maintenance mode"
mkdir -p "$ROOT"
touch "$ROOT/maintenance"
# Stop timers AND their services: stopping a timer only prevents future
# starts — an already-running oneshot keeps reading/writing the DB (PR #190
# review P1). Then wait for the service set to drain before the backup.
SERVICES=""
for u in $KAYAK_UNITS; do
    systemctl stop "$u" 2>/dev/null || true
    case "$u" in
        *.timer)
            svc="${u%.timer}.service"
            SERVICES="$SERVICES $svc"
            systemctl stop "$svc" 2>/dev/null || true
            ;;
        *.service) SERVICES="$SERVICES $u" ;;
    esac
done
waited=0
for svc in $SERVICES; do
    while systemctl is-active --quiet "$svc" 2>/dev/null; do
        if [ "$waited" -ge 120 ]; then
            echo "Error: $svc still active after ${waited}s — refusing to mutate the DB under it" >&2
            exit 1
        fi
        sleep 2
        waited=$((waited + 2))
    done
done
log "consumers quiesced"

log "backing up DB before mutation"
sqlite3 "$DB_PATH" ".backup '$PRE_BACKUP'"

LEVELS="$RELEASE_DIR/venv/bin/levels"
MUTATED=1
log "applying schema migrations"
DATABASE_URL="sqlite:///$DB_PATH" "$LEVELS" migrate

log "applying metadata sync (all-or-nothing)"
DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" "$LEVELS" sync-metadata

# Geometry/gradient sidecars are dataset content EXCLUDED from reach.csv —
# sync-metadata never writes reach.geom/gradient_profile. Without this step
# a sidecar-only dataset release would activate while serving stale geometry
# (PR #190 review P1). Rollback is covered by the pre-activation DB backup.
log "applying geometry/gradient sidecars"
DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" "$LEVELS" import-metadata

log "building docroot inside the release"
DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" \
    OUTPUT_DIR="$RELEASE_DIR/docroot" "$LEVELS" build

# Canonical runtime config for PHP: same path, same root wrapper, same
# secret-merge boundary as scripts/deploy.sh (PR #190 review P1 — config
# changes must actually reach PHP, and ONLY through the wrapper that merges
# root-only secrets and installs 0640 root:www-data). Emitted with FINAL
# release paths, not scratch paths.
log "installing canonical runtime config via the root wrapper"
DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" \
    OUTPUT_DIR="$RELEASE_DIR/docroot" "$LEVELS" emit-config --dry-run \
    | /usr/local/sbin/kayak-install-runtime-config

log "switching $ROOT/current -> releases/$RELEASE_ID (atomic)"
ln -s "releases/$RELEASE_ID" "$ROOT/current.new"
mv -f "$ROOT/current.new" "$ROOT/current"
SWITCHED=1

if [ -n "$HEALTH_URL" ]; then
    log "health check: $HEALTH_URL"
    curl -fsS --max-time 15 "$HEALTH_URL" >/dev/null
fi

log "starting consumers + leaving maintenance mode"
for u in $KAYAK_UNITS; do systemctl start "$u" 2>/dev/null || true; done
rm -f "$ROOT/maintenance"
trap - ERR

log "activated release $RELEASE_ID (engine $ENGINE_REF, dataset $DATASET_REF)"
