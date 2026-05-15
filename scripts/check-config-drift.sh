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
# into telemetry: see docs/PLAN_pre_release_followup.md §T1.2.

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

REPO="${REPO:-${KAYAK_HOME}/kayak}"

# Manifest of (repo_path, install_path) pairs. Tab-separated; lines starting
# with # are comments. Files NOT in this manifest:
#   - conf/levels.nginx, deploy/levels — retired; the three-vhost split
#     under conf/sites/ replaces these.
#   - deploy/secrets.env.example, deploy/msmtprc.example — example templates,
#     not deployed.
#   - deploy/install-config.sh, deploy/SETUP.md, systemd/install.service.sh —
#     admin tooling / docs. (install-config.sh was renamed from
#     install-secrets.sh in T3.3 Phase 5.2.)
#   - systemd/*.sh — helper scripts run in-tree from /home/pat/kayak/systemd/,
#     not copied to /etc/.
read -r -d '' MANIFEST <<'EOF' || true
# nginx
conf/security-headers.conf	/etc/nginx/snippets/security-headers.conf
conf/security-headers-turnstile.conf	/etc/nginx/snippets/security-headers-turnstile.conf
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
deploy/kayak-pipeline.sudoers	/etc/sudoers.d/kayak-pipeline
deploy/sudoers.d/kayak-emit-config	/etc/sudoers.d/kayak-emit-config
deploy/kayak-env.example	/etc/kayak/env
deploy/msmtp-aliases	/etc/msmtp-aliases
# sysctl, apt, sshd
deploy/sysctl.d/90-hardening.conf	/etc/sysctl.d/90-hardening.conf
deploy/sysctl.d/90-swap.conf	/etc/sysctl.d/90-swap.conf
deploy/sysctl.d/92-local-hardening.conf	/etc/sysctl.d/92-local-hardening.conf
deploy/sysctl.d/99-hardening.conf	/etc/sysctl.d/99-hardening.conf
deploy/apt.conf.d/50unattended-upgrades-local	/etc/apt/apt.conf.d/50-unattended-upgrades-local
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
    if cmp -s "$repo_abs" "$install"; then
        ok=$((ok + 1))
        return
    fi
    warn "DIFFERS: $install vs $repo_rel"
    diff -u "$repo_abs" "$install" || true
    drift=$((drift + 1))
}

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
