#!/usr/bin/env bash
# Detect drift between the in-repo config files and their installed copies
# under /etc/ (plus a few /etc/-adjacent paths). Runs weekly as
# kayak-config-drift.service.
#
# Exits 0 if every tracked file matches its installed copy.
# Exits 1 if any file differs OR is missing on disk (drift detected).
# Exits 2 on script-level error (missing repo file, malformed manifest).
#
# Some target paths aren't world-readable (sshd_config.d, sudoers.d), so the
# service unit runs as User=root. The script itself never writes anything;
# it's pure diff + reporting.
#
# Why this exists: the 2026-05-10 preflight pass found drift between repo
# templates and live /etc/ files (e.g. tuned values clobbered by a stale
# `sudo cp deploy/X /etc/Y`). Catching drift weekly turns silent regressions
# into telemetry: see docs/done/PLAN_pre_release_followup.md §T1.2.

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

REPO="${REPO:-${KAYAK_HOME}/kayak}"

# Manifest of (repo_path, install_path) pairs. Tab-separated; lines starting
# with # are comments. Files NOT in this manifest:
#   - conf/levels.nginx, deploy/levels — removed (the three-vhost split under
#     conf/sites/ + conf/snippets/ replaced these monoliths).
#   - deploy/secrets.env.example, deploy/msmtprc.example — example templates,
#     not deployed.
#   - deploy/install-config.sh, deploy/SETUP.md, systemd/install.service.sh —
#     admin tooling / docs. (install-config.sh was renamed from
#     install-secrets.sh in T3.3 Phase 5.2.)
#   - systemd/*.sh — helper scripts run in-tree from /home/pat/kayak/systemd/,
#     not copied to /etc/.
# RENDER-NORMALIZED files (see $RENDER_NORMALIZED below): levels-common.conf and
# kayak-fpm-pool.conf carry two lines rendered from host.yaml by the 4C cutover,
# so they're compared with only those lines masked — NOT dropped from tracking.
read -r -d '' MANIFEST <<'EOF' || true
# nginx
conf/security-headers.conf	/etc/nginx/snippets/security-headers.conf
conf/security-headers-turnstile.conf	/etc/nginx/snippets/security-headers-turnstile.conf
conf/mime-extras.conf	/etc/nginx/conf.d/mime-extras.conf
conf/snippets/levels-common.conf	/etc/nginx/snippets/levels-common.conf
conf/sites/levels-mousebrains-com	/etc/nginx/sites-available/levels-mousebrains-com
conf/sites/levels-test-wkcc-org	/etc/nginx/sites-available/levels-test-wkcc-org
conf/sites/levels-wkcc-org	/etc/nginx/sites-available/levels-wkcc-org
deploy/nginx-default-server	/etc/nginx/sites-available/default
deploy/nginx-editor-env.conf	/etc/nginx/conf.d/editor-env.conf
deploy/kayak-log-format.conf	/etc/nginx/conf.d/kayak-log-format.conf
deploy/ratelimit.conf	/etc/nginx/conf.d/ratelimit.conf
# php-fpm
deploy/kayak-fpm-pool.conf	/etc/php/8.4/fpm/pool.d/kayak.conf
deploy/php-fpm-secrets-dropin.conf	/etc/systemd/system/php8.4-fpm.service.d/kayak-secrets.conf
# fail2ban
deploy/fail2ban/jail.local	/etc/fail2ban/jail.local
deploy/fail2ban/jail.d/kayak-edit.conf	/etc/fail2ban/jail.d/kayak-edit.conf
deploy/fail2ban/jail.d/kayak-editor-auth.conf	/etc/fail2ban/jail.d/kayak-editor-auth.conf
deploy/fail2ban/filter.d/nginx-default-block.conf	/etc/fail2ban/filter.d/nginx-default-block.conf
deploy/fail2ban/filter.d/nginx-edit-auth.conf	/etc/fail2ban/filter.d/nginx-edit-auth.conf
deploy/fail2ban/filter.d/nginx-editor-auth.conf	/etc/fail2ban/filter.d/nginx-editor-auth.conf
deploy/fail2ban/filter.d/nginx-malicious.conf	/etc/fail2ban/filter.d/nginx-malicious.conf
# logrotate + misc
deploy/logrotate-kayak-csp	/etc/logrotate.d/kayak-csp
deploy/nftables.conf	/etc/nftables.conf
deploy/sudoers.d/kayak-pipeline	/etc/sudoers.d/kayak-pipeline
deploy/sudoers.d/kayak-emit-config	/etc/sudoers.d/kayak-emit-config
deploy/kayak-install-runtime-config.sh	/usr/local/sbin/kayak-install-runtime-config
deploy/kayak-env.example	/etc/kayak/env
deploy/msmtp-aliases	/etc/msmtp-aliases
# sysctl, apt, sshd
deploy/sysctl.d/90-hardening.conf	/etc/sysctl.d/90-hardening.conf
deploy/sysctl.d/90-swap.conf	/etc/sysctl.d/90-swap.conf
deploy/sysctl.d/92-local-hardening.conf	/etc/sysctl.d/92-local-hardening.conf
deploy/sysctl.d/99-hardening.conf	/etc/sysctl.d/99-hardening.conf
deploy/apt.conf.d/50unattended-upgrades-local	/etc/apt/apt.conf.d/50unattended-upgrades-local
deploy/sshd_config.d/hardening.conf	/etc/ssh/sshd_config.d/hardening.conf
EOF

# Auto-generate the systemd unit mapping (every .service / .timer file).
SYSTEMD_MANIFEST=$(
    for f in "$REPO"/systemd/*.service "$REPO"/systemd/*.timer; do
        [ -e "$f" ] || continue
        rel="${f#$REPO/}"
        name="$(basename "$f")"
        printf '%s\t/etc/systemd/system/%s\n' "$rel" "$name"
    done
)

# Files whose LIVE copy legitimately diverges from the generic repo template
# because the 4C paired-release cutover (deploy/INSTALL-paired-release.md)
# RENDERS two lines from /etc/kayak/host.yaml via `levels render-serving`:
#   - the served-docroot nginx `root` (NOT the ACME `root /var/www/certbot;`)
#   - the PHP-FPM pool `open_basedir`
# For these, mask ONLY those lines before comparing, so the rest of each file
# (security headers, FPM socket/user/limits, …) is still byte-compared and a
# real regression is still caught. A pre-cutover host (no host.yaml render)
# matches byte-for-byte too — the mask is a no-op when the lines already agree.
#
# KNOWN LIMITATION (PR #198 review #1): masking the WHOLE line means this weekly
# check no longer verifies the *content* of `root` / `open_basedir` — only that
# everything else matches. A manual tamper of just those two lines (e.g. widening
# `open_basedir` with `/tmp:`) would NOT be flagged here. They are still verified,
# but only at DEPLOY time, by the deployer's serving-path gate (kayak-deploy.sh,
# SERVING_CUTOVER) — not weekly. The follow-up that closes the continuous-
# monitoring gap is to compare the live lines against `levels render-serving`
# output (drift = "live line ≠ what host.yaml renders") instead of blanking them;
# deferred because it couples this check to the renderer + host.yaml + a `levels`
# binary.
#
# Path duplication (PR #198 review #2): the FPM pool path below ALSO appears in
# MANIFEST above. A PHP-version bump (php8.4 → php8.5) must update BOTH lines;
# changing only MANIFEST silently re-introduces the false `open_basedir` drift.
RENDER_NORMALIZED="/etc/nginx/snippets/levels-common.conf /etc/php/8.4/fpm/pool.d/kayak.conf"
normalize_rendered() {
    sed -E \
        -e '\%^[[:space:]]*root[[:space:]]+/var/www/certbot;%! s%^([[:space:]]*)root[[:space:]]+[^;]*;%\1root @@RENDERED@@;%' \
        -e 's%^([[:space:]]*)php_admin_value\[open_basedir\][[:space:]]*=.*%\1php_admin_value[open_basedir] = @@RENDERED@@%' \
        "$1"
}

log()  { printf '[drift-check] %s\n' "$*"; }
warn() { printf '[drift-check] DRIFT: %s\n' "$*"; }

ok=0
drift=0
missing=0

check_one() {
    local repo_rel=$1
    local install=$2
    local repo_abs="$REPO/$repo_rel"

    if [ ! -f "$repo_abs" ]; then
        log "ERROR: manifest references $repo_rel but it doesn't exist in the repo"
        exit 2
    fi
    if [ ! -e "$install" ]; then
        warn "MISSING on disk: $install (from $repo_rel)"
        missing=$((missing + 1))
        return
    fi
    # Render-normalized files: compare with the host-rendered lines masked.
    case " $RENDER_NORMALIZED " in
        *" $install "*)
            if diff -q <(normalize_rendered "$repo_abs") <(normalize_rendered "$install") >/dev/null 2>&1; then
                ok=$((ok + 1))
                return
            fi
            warn "DIFFERS (host-rendered lines excluded): $install vs $repo_rel"
            diff -u <(normalize_rendered "$repo_abs") <(normalize_rendered "$install") || true
            drift=$((drift + 1))
            return
            ;;
    esac
    if cmp -s "$repo_abs" "$install"; then
        ok=$((ok + 1))
        return
    fi
    warn "DIFFERS: $install vs $repo_rel"
    diff -u "$repo_abs" "$install" || true
    drift=$((drift + 1))
}

# When SOURCED for unit tests (KAYAK_DRIFT_LIB=1), stop here: the functions above
# (normalize_rendered, check_one) are the unit under test, and the manifest walk
# below reads this host's live /etc. A normal `check-config-drift.sh` run leaves
# KAYAK_DRIFT_LIB unset and proceeds. See tests/test_scripts/test_config_drift.py.
if [ "${KAYAK_DRIFT_LIB:-}" = "1" ]; then
    return 0
fi

# Walk the explicit manifest, skipping blank lines and # comments.
while IFS=$'\t' read -r repo_rel install; do
    [ -z "$repo_rel" ] && continue
    case "$repo_rel" in \#*) continue ;; esac
    check_one "$repo_rel" "$install"
done <<< "$MANIFEST"

# Walk the auto-generated systemd manifest.
while IFS=$'\t' read -r repo_rel install; do
    [ -z "$repo_rel" ] && continue
    check_one "$repo_rel" "$install"
done <<< "$SYSTEMD_MANIFEST"

total=$((ok + drift + missing))
log "Checked $total file(s): $ok match, $drift differ, $missing missing"

if [ "$drift" -gt 0 ] || [ "$missing" -gt 0 ]; then
    exit 1
fi
exit 0
