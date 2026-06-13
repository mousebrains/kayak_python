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
#   KAYAK_APP_USER                 service account owning the DB/docroot;
#                                  REQUIRED for a root-run activation (DB/build
#                                  steps run as this user via runuser)
#   SERVING_CUTOVER                must be "yes" to activate (set by the 4C
#                                  runbook once nginx/FPM point at current)
# Path overrides (mainly for tests / the clean-VM rehearsal):
#   KAYAK_DEPLOY_ROOT              release root (default /opt/kayak)
#   KAYAK_RUNTIME_CONFIG           PHP config path (default /etc/kayak/runtime-config.json)
#   KAYAK_CONFIG_INSTALLER         root config wrapper path
#   KAYAK_SYSTEMCTL                systemctl path
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

# Resolve the SAME host environment the systemd consumers get, so the staged
# emit-config and the activation steps see SITE_URL / SQLITE_PATH / DATASET_DIR
# / OUTPUT_DIR identically (PR #190 live review P1: on the WKCC host these are
# NOT in /etc/kayak/env — that file holds only KAYAK_HOME — they live in the
# app user's ~/.config/kayak/.env, which the units load via a second
# EnvironmentFile=). `set -a` exports them so subprocesses inherit them, the
# way EnvironmentFile does. The clean paired-release host puts everything in
# /etc/kayak/env; sourcing both makes the deployer correct either way.
KAYAK_HOST_ENV="${KAYAK_HOST_ENV:-/etc/kayak/env}"
if [ -r "$KAYAK_HOST_ENV" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$KAYAK_HOST_ENV"
    set +a
fi
# Second env file: the app user's ~/.config/kayak/.env (KAYAK_APP_ENV overrides;
# resolved from KAYAK_APP_USER's home when unset). Reading a 0600 app-user file
# as the root orchestrator is fine.
APP_ENV="${KAYAK_APP_ENV:-}"
if [ -z "$APP_ENV" ] && [ -n "${KAYAK_APP_USER:-}" ] && command -v getent >/dev/null 2>&1; then
    _app_home="$(getent passwd "$KAYAK_APP_USER" | cut -d: -f6)"
    [ -n "$_app_home" ] && APP_ENV="$_app_home/.config/kayak/.env"
fi
if [ -n "$APP_ENV" ] && [ -r "$APP_ENV" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$APP_ENV"
    set +a
fi

: "${ENGINE_REPO:?ENGINE_REPO must be set in $CONF (git URL/path of kayak_python)}"
: "${DATASET_REPO:?DATASET_REPO must be set in $CONF (git URL/path of kayak_data)}"
: "${ENGINE_BRANCH:=main}"
: "${DATASET_BRANCH:=main}"
: "${KAYAK_UNITS:=kayak-pipeline.timer kayak-backup-hourly.timer kayak-backup-weekly.timer kayak-decimate.timer kayak-status.timer kayak-fetch-osmb.timer kayak-editor-retention.timer kayak-audit-gauges.timer}"
: "${HEALTH_URL:=}"
ROOT="${KAYAK_DEPLOY_ROOT:-/opt/kayak}"
RUNTIME_CONFIG="${KAYAK_RUNTIME_CONFIG:-/etc/kayak/runtime-config.json}"
CONFIG_INSTALLER="${KAYAK_CONFIG_INSTALLER:-/usr/local/sbin/kayak-install-runtime-config}"
# Parameterized so the activation path is testable without root/systemd
# (tests point this at a recording stub).
SYSTEMCTL="${KAYAK_SYSTEMCTL:-systemctl}"

# Privilege model (PR #190 third-round review): ONE orchestrator mode. The
# orchestrator itself is root on a real host (systemctl + the root config
# installer); the steps that WRITE PERSISTENT APP STATE the rest of the
# system owns — the DB and its WAL sidecars, the built docroot — run as
# KAYAK_APP_USER via runuser so root never creates app-owned-resource
# sidecars (the WAL footgun). Read-only/scratch staging (wheel build, dataset
# validate, the normalized-digest emit) runs as the orchestrator: it only
# writes the root-owned scratch dir and never touches the live DB, and the
# secret filter below — not the uid — is what keeps credentials out of the
# retained release copy. Run unprivileged (--stage-only, tests), run_app is a
# pass-through. The root/runuser env propagation is validated end-to-end in
# the Batch 4C clean-VM rehearsal (it needs real root + systemd).
: "${KAYAK_APP_USER:=}"
# The privilege decision and the privilege-drop command are overridable so the
# root branch is testable without real root: KAYAK_PRIVILEGED=yes forces it,
# KAYAK_RUNUSER points at a same-user shim. Default: privileged iff uid 0,
# dropping via runuser.
: "${KAYAK_PRIVILEGED:=auto}"
RUNUSER="${KAYAK_RUNUSER:-runuser}"
is_privileged() {
    case "$KAYAK_PRIVILEGED" in
        yes) return 0 ;;
        no) return 1 ;;
        *) [ "$(id -u)" -eq 0 ] ;;
    esac
}
run_app() {
    if is_privileged; then
        if [ -z "$KAYAK_APP_USER" ]; then
            echo "Error: privileged activation requires KAYAK_APP_USER in $CONF (the service user that owns the DB/docroot)" >&2
            exit 1
        fi
        "$RUNUSER" -u "$KAYAK_APP_USER" -- "$@"
    else
        "$@"
    fi
}

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
APP_SCRATCH=""
CLEAN_SCRATCH=1
cleanup() {
    # Must always end with a zero status: this is the EXIT trap, and a failing
    # final command here would override the script's real exit code.
    if [ "$CLEAN_SCRATCH" = 1 ]; then
        rm -rf "$SCRATCH"
        if [ -n "$APP_SCRATCH" ] && [ "$APP_SCRATCH" != "$SCRATCH" ]; then
            rm -rf "$APP_SCRATCH"
        fi
    fi
    return 0
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

# Atomically repoint the symlink $2 at a new target $1, replacing an existing
# symlink WITHOUT dereferencing it. The naive `ln -s tgt x.new && mv -f x.new
# link` is a footgun: when `link` already exists as a symlink TO A DIRECTORY,
# both GNU and BSD `mv` follow it and move `x.new` INTO that directory, leaving
# `link` pointing at the OLD release. (The first cutover works — no prior
# `current` — but every subsequent one silently no-ops, and prune then GCs the
# unreferenced new release.) The portable cure is the "don't treat the target
# as a directory" flag: GNU mv spells it -T/--no-target-directory, BSD/macOS mv
# spells it -h. Try GNU first, then BSD; both perform an atomic rename(2). Only
# an mv that supports neither falls back to a (non-atomic) remove-then-move.
atomic_relink() {
    _tgt="$1"; _link="$2"; _tmp="$2.swap.$$"
    rm -f "$_tmp"
    ln -s "$_tgt" "$_tmp"
    if mv -fT "$_tmp" "$_link" 2>/dev/null; then return 0; fi
    if mv -fh "$_tmp" "$_link" 2>/dev/null; then return 0; fi
    rm -f "$_link"
    mv -f "$_tmp" "$_link"
}

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
    # The --single-branch bare clone already contains every ancestor of the
    # branch tip, so any reachable ref resolves locally — no extra fetch needed.
    # Confirm the ref exists, then that it is reachable from the protected branch.
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
# --exclude-secrets drops every SecretStr field at the source (type-based, so
# a future secret field can't leak by name); the normalize pass then only
# strips staging-local PATH fields and operational tokens for digest stability.
# Belt-and-suspenders: the name filter stays as a second line of defense.
log "emitting runtime config (normalized digest)"
DATASET_DIR="$SCRATCH/dataset" OUTPUT_DIR="$SCRATCH/dataset" \
    "$STAGE_LEVELS" emit-config --exclude-secrets --out "$SCRATCH/runtime-config-raw.json"
normalize_config() { # <in> <out>: drop path-local + token fields, sort keys
    "$PYTHON" - "$1" "$2" <<'PYNORM'
import json
import sys

drop = {"dataset_dir", "output_dir", "osmb_dir", "map_layers_dir",
        "gauge_metadata_cache", "database_path", "database_url", "ntfy_topic"}


def keep(k):
    if k in drop or k.startswith("hc_"):
        return False
    # SecretStr fields are already gone (emit-config --exclude-secrets); this
    # name filter is the second line of defense (PR #190 third + live reviews).
    lowered = k.lower()
    return not any(s in lowered for s in ("secret", "password", "token"))


data = json.load(open(sys.argv[1]))
data = {k: v for k, v in data.items() if keep(k)}
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
    log "release $RELEASE_ID already staged — re-verifying retained artifacts"
    # Reuse must FAIL CLOSED (PR #190 4th-round P2): a stale/corrupted release
    # dir could otherwise mutate the live DB from unverified dataset/config.
    # verify_release (below) checks every digestable artifact against the
    # manifest AND the recomputed inputs; re-extract the dataset from the
    # freshly-verified tar so the on-disk snapshot can't have drifted.
    rm -rf "$RELEASE_DIR/dataset"
    mkdir -p "$RELEASE_DIR/dataset"
    tar -xf "$SCRATCH/dataset.tar" -C "$RELEASE_DIR/dataset"
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

# Full-manifest verification (fresh stage self-checks; reuse fails closed) —
# every digestable retained artifact vs both the manifest and the recomputed
# inputs, plus a live check that the release venv runs. The dataset tar digest
# is compared to the manifest; the on-disk dataset was just (re-)extracted
# from that verified tar.
"$PYTHON" - "$RELEASE_DIR" "$WHEEL_SHA" "$LOCK_SHA" "$CONFIG_SHA" "$DATASET_TAR_SHA" <<'PYVERIFY'
import hashlib
import json
import pathlib
import sys

rel = pathlib.Path(sys.argv[1])
wheel_sha, lock_sha, cfg_sha, tar_sha = sys.argv[2:6]
m = json.load((rel / "release.json").open())


def digest(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


bad = []
# (file, manifest-key, recomputed-this-run)
for fname, key, recomputed in (
    (m["wheel"], "wheel_sha256", wheel_sha),
    ("requirements-prod.lock", "requirements_lock_sha256", lock_sha),
    ("runtime-config.json", "runtime_config_sha256", cfg_sha),
):
    p = rel / fname
    if not p.exists():
        bad.append(f"missing retained artifact: {fname}")
        continue
    on_disk = digest(p)
    if on_disk != m[key]:
        bad.append(f"{fname}: on-disk {on_disk[:12]} != manifest {m[key][:12]}")
    if m[key] != recomputed:
        bad.append(f"{fname}: manifest {m[key][:12]} != recomputed input {recomputed[:12]}")
if m.get("dataset_tar_sha256") != tar_sha:
    bad.append("dataset: manifest tar digest != recomputed dataset archive")
if not (rel / "venv" / "bin" / "levels").exists():
    bad.append("release venv missing venv/bin/levels")
if bad:
    sys.stderr.write("RELEASE VERIFY FAILED (remove the dir to force a clean restage):\n  ")
    sys.stderr.write("\n  ".join(bad) + "\n")
    sys.exit(1)
PYVERIFY

log "staged: $RELEASE_DIR"
if [ "$STAGE_ONLY" = 1 ]; then
    log "stage-only requested — stopping before activation"
    echo "$RELEASE_DIR"
    exit 0
fi

# Activation gate (PR #190 review P1): switching $ROOT/current is only
# meaningful once the host is fully cut over to the paired-release layout —
# BOTH the web serving config (nginx root, FPM open_basedir/KAYAK_CONFIG_PATH)
# AND the systemd consumers must run from $ROOT/current. That re-pointing is
# the Batch 4C cutover, recorded by the installer as SERVING_CUTOVER in
# deploy.env. Refusing here keeps this deployer from reporting success while
# users are served the legacy docroot or the next pipeline run executes the
# old checkout against the freshly migrated DB.
if [ "${SERVING_CUTOVER:-no}" != "yes" ]; then
    echo "Error: this host is not cut over to the $ROOT/current paired-release layout" >&2
    echo "(SERVING_CUTOVER=yes not set in $CONF — done by the Batch 4C install/migration" >&2
    echo "runbook). Use --stage-only, or deploy with scripts/deploy.sh until then." >&2
    exit 1
fi

# Verify the consumers actually run from the release (PR #190 reviews): a
# consumer still pointing at the old checkout/venv would execute old code
# against the freshly migrated DB + synced dataset. EVERY consumer service
# must run from $ROOT/current UNLESS it is an explicit host-level unit
# (KAYAK_HOST_UNITS — e.g. the backup scripts, which legitimately stay
# host-level). Keying off $ROOT/current + an exemption list, NOT a `levels`
# substring (which missed kayak-audit-gauges, a python-run engine consumer).
: "${KAYAK_HOST_UNITS:=kayak-backup-hourly.service kayak-backup-weekly.service kayak-backup-offsite.service}"
for u in $KAYAK_UNITS; do
    case "$u" in
        *.timer) svc="${u%.timer}.service" ;;
        *) svc="$u" ;;
    esac
    case " $KAYAK_HOST_UNITS " in
        *" $svc "*) continue ;;
    esac
    es="$("$SYSTEMCTL" show -p ExecStart --value "$svc" 2>/dev/null || true)"
    # A unit with no ExecStart (not installed / pure timer target) has nothing
    # to verify; a present ExecStart must reference the release.
    [ -z "$es" ] && continue
    case "$es" in
        *"$ROOT/current"*) : ;;
        *)
            echo "Error: $svc does not run from $ROOT/current (ExecStart: $es)." >&2
            echo "Re-render it to the paired-release layout (Batch 4C), or add it to" >&2
            echo "KAYAK_HOST_UNITS in $CONF if it is a deliberate host-level unit." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Phase 3 — activate (system mutation; everything before the symlink switch
# is undone by the rollback path)
# ---------------------------------------------------------------------------
PREV_TARGET=""
if [ -L "$ROOT/current" ]; then
    PREV_TARGET="$(readlink "$ROOT/current")"
fi

# SQLITE_PATH was resolved into the env by the host-env sourcing above
# (KAYAK_HOST_ENV + the app user's .config). A clear early failure if the host
# env model didn't supply it, rather than an obscure abort mid-activation.
DB_PATH="${SQLITE_PATH:-}"
if [ -z "$DB_PATH" ]; then
    echo "Error: SQLITE_PATH is not set — it must come from the host env" >&2
    echo "($KAYAK_HOST_ENV or the app user's ~/.config/kayak/.env). Activation needs the DB path." >&2
    exit 1
fi

# The DB backup + restore must run as the app user (own the DB/WAL sidecars),
# so it lands in an APP-OWNED scratch dir — the orchestrator's mktemp -d is
# 0700 root, untraversable by the app user (PR #190 4th-round P1). Only the DB
# backup goes here; no secrets ever do.
if is_privileged && [ -n "$KAYAK_APP_USER" ]; then
    APP_SCRATCH="$("$RUNUSER" -u "$KAYAK_APP_USER" -- mktemp -d)"
else
    APP_SCRATCH="$SCRATCH"
fi
PRE_BACKUP="$APP_SCRATCH/pre-activate.db"
MUTATED=0

SWITCHED=0
CONFIG_INSTALLED=0
rollback() {
    status=$?
    trap - ERR
    echo "kayak-deploy: FAILURE (exit $status) — rolling back" >&2
    if [ "$CONFIG_INSTALLED" = 1 ] && [ -f "$SCRATCH/runtime-config.prev" ]; then
        echo "kayak-deploy: restoring previous runtime config" >&2
        if ! cp -p "$SCRATCH/runtime-config.prev" "$RUNTIME_CONFIG"; then
            echo "kayak-deploy: CONFIG RESTORE FAILED — recover from $SCRATCH/runtime-config.prev" >&2
            CLEAN_SCRATCH=0
        fi
    fi
    if [ "$MUTATED" = 1 ] && [ -f "$PRE_BACKUP" ]; then
        echo "kayak-deploy: restoring pre-activation DB backup" >&2
        # Restore as the app user too — a root .restore would recreate the
        # root-owned WAL/SHM footgun the run_app model exists to avoid.
        run_app sqlite3 "$DB_PATH" ".restore '$PRE_BACKUP'" || \
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
        atomic_relink "$PREV_TARGET" "$ROOT/current" || \
            echo "kayak-deploy: WARNING: could not restore current -> $PREV_TARGET" >&2
        echo "kayak-deploy: current -> $PREV_TARGET (previous release)" >&2
    fi
    for u in $KAYAK_UNITS; do "$SYSTEMCTL" start "$u" 2>/dev/null || true; done
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
    "$SYSTEMCTL" stop "$u" 2>/dev/null || true
    case "$u" in
        *.timer)
            svc="${u%.timer}.service"
            SERVICES="$SERVICES $svc"
            "$SYSTEMCTL" stop "$svc" 2>/dev/null || true
            ;;
        *.service) SERVICES="$SERVICES $u" ;;
    esac
done
waited=0
for svc in $SERVICES; do
    while "$SYSTEMCTL" is-active --quiet "$svc" 2>/dev/null; do
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
# The build writes the docroot inside the release; chown it so the app-user
# build can write there (the release tree itself was created by the
# orchestrator). PRE_BACKUP already lives in the app-owned scratch.
if is_privileged && [ -n "$KAYAK_APP_USER" ]; then
    chown "$KAYAK_APP_USER" "$RELEASE_DIR/docroot"
fi
run_app sqlite3 "$DB_PATH" ".backup '$PRE_BACKUP'"

LEVELS="$RELEASE_DIR/venv/bin/levels"
MUTATED=1
log "applying schema migrations"
run_app env DATABASE_URL="sqlite:///$DB_PATH" "$LEVELS" migrate

log "applying metadata sync (all-or-nothing)"
run_app env DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" "$LEVELS" sync-metadata

# Geometry/gradient sidecars are dataset content EXCLUDED from reach.csv —
# sync-metadata never writes reach.geom/gradient_profile. Without this step
# a sidecar-only dataset release would activate while serving stale geometry
# (PR #190 review P1). Rollback is covered by the pre-activation DB backup.
log "applying geometry/gradient sidecars"
run_app env DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" "$LEVELS" import-metadata

log "building docroot inside the release"
run_app env DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" \
    OUTPUT_DIR="$RELEASE_DIR/docroot" "$LEVELS" build

# Canonical runtime config for PHP: same path, same root wrapper, same
# secret-merge boundary as scripts/deploy.sh (PR #190 review P1 — config
# changes must actually reach PHP, and ONLY through the wrapper that merges
# root-only secrets and installs 0640 root:www-data). Emitted with FINAL
# release paths, not scratch paths.
log "installing canonical runtime config via the root wrapper"
if [ -f "$RUNTIME_CONFIG" ]; then
    # Snapshot for rollback: a failed switch/health check must restore the
    # config PHP serves, not leave the old release running with the failed
    # release's config (PR #190 third-round P1).
    cp -p "$RUNTIME_CONFIG" "$SCRATCH/runtime-config.prev"
fi
run_app env DATABASE_URL="sqlite:///$DB_PATH" DATASET_DIR="$RELEASE_DIR/dataset" \
    OUTPUT_DIR="$RELEASE_DIR/docroot" "$LEVELS" emit-config --dry-run \
    | "$CONFIG_INSTALLER"
CONFIG_INSTALLED=1

log "switching $ROOT/current -> releases/$RELEASE_ID (atomic)"
atomic_relink "releases/$RELEASE_ID" "$ROOT/current"
SWITCHED=1

if [ -n "$HEALTH_URL" ]; then
    log "health check: $HEALTH_URL"
    curl -fsS --max-time 15 "$HEALTH_URL" >/dev/null
fi

log "starting consumers + leaving maintenance mode"
for u in $KAYAK_UNITS; do "$SYSTEMCTL" start "$u" 2>/dev/null || true; done
rm -f "$ROOT/maintenance"
trap - ERR

# Prune old releases — each carries a full venv, so without a retention bound
# they accumulate unbounded on the VPS (PR #190 live review P2). Keep the
# KAYAK_KEEP_RELEASES most-recent, but NEVER the active one or the previous
# (rollback needs PREV_TARGET). Done after activation so a failure above never
# deletes a release.
: "${KAYAK_KEEP_RELEASES:=5}"
prune_releases() {
    cur="$(basename "$(readlink "$ROOT/current")")"
    prev="$([ -n "$PREV_TARGET" ] && basename "$PREV_TARGET" || echo "")"
    # Newest-first by mtime; skip the keep window, current, and previous.
    # Release ids are 12-hex (no spaces/newlines), so word-splitting ls -t is
    # safe here and gives the recency order a glob can't.
    kept=0
    # shellcheck disable=SC2045
    for d in $(ls -1dt "$ROOT/releases/"*/ 2>/dev/null); do
        id="$(basename "$d")"
        if [ "$id" = "$cur" ] || [ "$id" = "$prev" ]; then
            continue
        fi
        kept=$((kept + 1))
        if [ "$kept" -le "$KAYAK_KEEP_RELEASES" ]; then
            continue
        fi
        log "pruning old release $id"
        rm -rf "$d"
    done
}
prune_releases

log "activated release $RELEASE_ID (engine $ENGINE_REF, dataset $DATASET_REF)"
