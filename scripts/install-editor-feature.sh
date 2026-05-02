#!/usr/bin/env bash
#
# install-editor-feature.sh — Phase 1 sudo install steps for the Comment /
# Login feature.
#
# Does NOT touch the database. After this runs, you still need to:
#   1. Ensure new tables exist:   /home/pat/.venv/bin/levels init-db
#   2. Create your maintainer:    /home/pat/.venv/bin/levels seed-maintainer \
#                                    --email you@example.com --name "Your Name"
#   3. Flip the feature flag on when ready (see end of this script).
#
# Run as root:  sudo bash scripts/install-editor-feature.sh
#
# Safe to re-run. Backups of replaced files are written under
#   /var/backups/kayak-nginx/<UTC timestamp>/
# (never inside nginx's include paths — a .bak file in sites-enabled/ is
# parsed by nginx and yields duplicate listen errors).

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Must be run as root (try: sudo bash $0)" >&2
    exit 1
fi

REPO=/home/pat/kayak
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR=/var/backups/kayak-nginx/$STAMP
mkdir -p "$BACKUP_DIR"
echo "Backups for this run: $BACKUP_DIR"
echo

# Files we'll create fresh (no prior copy). Rollback deletes them.
NEW_FILES=()
# Files we replace. Rollback moves the backup back over them.
REPLACED_FILES=()

backup_replace() {
    # Install $2 at $1, backing up any existing file.
    local dest=$1 src=$2
    if [[ -e $dest ]]; then
        cp -a "$dest" "$BACKUP_DIR/$(basename "$dest")"
        echo "  backed up $dest"
        REPLACED_FILES+=("$dest")
    else
        NEW_FILES+=("$dest")
    fi
    install -m 0644 "$src" "$dest"
    echo "  installed $dest"
}

rollback() {
    echo "ABORT: rolling back." >&2
    for f in "${REPLACED_FILES[@]-}"; do
        [[ -n ${f:-} ]] || continue
        local bak
        bak=$BACKUP_DIR/$(basename "$f")
        if [[ -e $bak ]]; then
            install -m 0644 "$bak" "$f"
            echo "  restored $f"
        fi
    done
    for f in "${NEW_FILES[@]-}"; do
        [[ -n ${f:-} ]] || continue
        rm -f "$f"
        echo "  removed $f"
    done
}

echo "=== nginx security-headers snippets ==="
mkdir -p /etc/nginx/snippets
backup_replace /etc/nginx/snippets/security-headers.conf "$REPO/conf/security-headers.conf"
backup_replace /etc/nginx/snippets/security-headers-turnstile.conf "$REPO/conf/security-headers-turnstile.conf"

echo
echo "=== nginx rate-limit zones ==="
backup_replace /etc/nginx/conf.d/ratelimit.conf "$REPO/deploy/nginx-ratelimit.conf"

echo
echo "=== nginx editor-feature env map ==="
# Do NOT overwrite an existing editor-env.conf: the operator edits it to
# flip the feature flag and set the Turnstile site key, and those
# customizations must survive a re-run of this script.
if [[ -e /etc/nginx/conf.d/editor-env.conf ]]; then
    echo "  /etc/nginx/conf.d/editor-env.conf already present — leaving it alone."
    echo "  (to reset to the repo defaults, remove the file and re-run)"
else
    install -m 0644 "$REPO/deploy/nginx-editor-env.conf" /etc/nginx/conf.d/editor-env.conf
    NEW_FILES+=(/etc/nginx/conf.d/editor-env.conf)
    echo "  installed /etc/nginx/conf.d/editor-env.conf"
fi

echo
echo "=== nginx site config ==="
backup_replace /etc/nginx/sites-enabled/levels "$REPO/conf/levels.nginx"

echo
echo "=== nginx syntax check ==="
if ! nginx -t; then
    rollback
    exit 1
fi

echo
echo "=== fail2ban filter for /login.php and /auth.php ==="
cat > /etc/fail2ban/filter.d/nginx-editor-auth.conf <<'EOF'
# Matches 4xx responses on the editor auth endpoints in nginx access log.
# Deliberately includes 200 on /login.php POST as well (each submit costs one
# magic-link send) so a single IP can't drip-feed email abuse.
[Definition]
failregex = ^<HOST> .* "(?:GET|POST) /(?:login|auth)\.php\b[^"]*" (?:40[0-9]|429)
            ^<HOST> .* "POST /login\.php\b[^"]*" 200
ignoreregex =
EOF
echo "  wrote /etc/fail2ban/filter.d/nginx-editor-auth.conf"

cat > /etc/fail2ban/jail.d/kayak-editor-auth.conf <<'EOF'
[nginx-editor-auth]
enabled  = true
filter   = nginx-editor-auth
logpath  = /var/log/nginx/kayak-access.log
maxretry = 10
findtime = 10m
bantime  = 1h
EOF
echo "  wrote /etc/fail2ban/jail.d/kayak-editor-auth.conf"

echo
echo "=== reload services ==="
systemctl reload nginx
echo "  nginx reloaded"
if systemctl is-active --quiet fail2ban; then
    systemctl reload fail2ban || systemctl restart fail2ban
    echo "  fail2ban reloaded"
else
    echo "  fail2ban not running — skipping reload"
fi

echo
echo "=== next steps (run as pat, not root) ==="
cat <<'EOF'

  1. Create / migrate new tables (idempotent, additive, does NOT overwrite
     any existing data):

       /home/pat/.venv/bin/levels init-db

  2. Seed yourself as the maintainer:

       /home/pat/.venv/bin/levels seed-maintainer \
           --email pat@mousebrains.com --name "Pat Welch"

  3. (Dev only) Wire up the feature for self-testing without real email:

       export MAIL_DUMP_DIR=/tmp/kayak-mail   # writes emails to files
       export EDITOR_FEATURE=1
       php -S 127.0.0.1:8000 -t public_html

  4. When ready to flip the feature flag on for the live site, edit
     /etc/nginx/conf.d/editor-env.conf and set:

       map $host $editor_feature     { default "1"; }
       map $host $turnstile_site_key { default "<your turnstile site key>"; }
       map $host $mail_from          { default "noreply@levels.wkcc.org"; }

     The Turnstile *secret* lives in /etc/kayak/secrets.env (mode 0600
     root:www-data) — never in nginx config. See deploy/install-secrets.sh.

     then:  sudo nginx -t && sudo systemctl reload nginx

  Installed file backups (if any) are tagged with the current UTC timestamp.

EOF
echo "Install complete."
