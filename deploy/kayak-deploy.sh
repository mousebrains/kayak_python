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
#   activate  maintenance on -> stop consumers -> DB backup -> migrate ->
#             all-or-nothing sync -> build docroot -> atomic symlink ->
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
"$PYTHON" -m venv "$SCRATCH/buildenv"
"$SCRATCH/buildenv/bin/pip" wheel --quiet --no-deps -w "$SCRATCH/dist" "$SCRATCH/engine-src"
WHEEL="$(ls "$SCRATCH"/dist/*.whl)"
WHEEL_SHA="$(sha256 "$WHEEL")"

log "snapshotting dataset at $DATASET_REF"
git -C "$SCRATCH/dataset.git" archive --format=tar -o "$SCRATCH/dataset.tar" "$DATASET_REF"
DATASET_TAR_SHA="$(sha256 "$SCRATCH/dataset.tar")"

# Host-config fingerprint: the non-secret host shape participates in the
# release identity so a host-config-only change is a new release.
HOST_YAML="${KAYAK_HOST_CONFIG:-/etc/kayak/host.yaml}"
if [ -r "$HOST_YAML" ]; then
    HOST_FP="$(sha256 "$HOST_YAML")"
else
    HOST_FP="none"
fi

RELEASE_ID="$(printf '%s %s %s' "$WHEEL_SHA" "$DATASET_REF" "$HOST_FP" \
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
    "$RELEASE_DIR/venv/bin/pip" install --quiet "$WHEEL"
    cp "$WHEEL" "$RELEASE_DIR/"

    LEVELS="$RELEASE_DIR/venv/bin/levels"
    log "validating dataset contract with the staged engine"
    "$LEVELS" validate-dataset "$RELEASE_DIR/dataset"

    log "emitting non-secret runtime config into the release"
    DATASET_DIR="$RELEASE_DIR/dataset" OUTPUT_DIR="$RELEASE_DIR/docroot" \
        "$LEVELS" emit-config --out "$RELEASE_DIR/runtime-config.json"

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
for u in $KAYAK_UNITS; do systemctl stop "$u" 2>/dev/null || true; done

log "backing up DB before mutation"
sqlite3 "$DB_PATH" ".backup '$PRE_BACKUP'"

LEVELS="$RELEASE_DIR/venv/bin/levels"
MUTATED=1
log "applying schema migrations"
DATABASE_URL="sqlite:///$DB_PATH" "$LEVELS" migrate

log "applying metadata sync (all-or-nothing)"
DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" "$LEVELS" sync-metadata

log "building docroot inside the release"
DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" \
    OUTPUT_DIR="$RELEASE_DIR/docroot" "$LEVELS" build

log "switching $ROOT/current -> releases/$RELEASE_ID (atomic)"
ln -s "releases/$RELEASE_ID" "$ROOT/current.new"
mv -f "$ROOT/current.new" "$ROOT/current"

if [ -n "$HEALTH_URL" ]; then
    log "health check: $HEALTH_URL"
    curl -fsS --max-time 15 "$HEALTH_URL" >/dev/null
fi

log "starting consumers + leaving maintenance mode"
for u in $KAYAK_UNITS; do systemctl start "$u" 2>/dev/null || true; done
rm -f "$ROOT/maintenance"
trap - ERR

log "activated release $RELEASE_ID (engine $ENGINE_REF, dataset $DATASET_REF)"
