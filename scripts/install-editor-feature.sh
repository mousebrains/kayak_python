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
# Safe to re-run. Existing files are backed up to *.bak.<UTC timestamp>.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Must be run as root (try: sudo bash $0)" >&2
    exit 1
fi

REPO=/home/pat/kayak
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

backup() {
    local path=$1
    if [[ -e $path ]]; then
        cp -a "$path" "$path.bak.$STAMP"
        echo "  backed up $path -> $path.bak.$STAMP"
    fi
}

echo "=== nginx rate-limit zones ==="
backup /etc/nginx/conf.d/ratelimit.conf
install -m 0644 "$REPO/deploy/nginx-ratelimit.conf" /etc/nginx/conf.d/ratelimit.conf
echo "  installed /etc/nginx/conf.d/ratelimit.conf"

echo
echo "=== nginx editor-feature env map ==="
backup /etc/nginx/conf.d/editor-env.conf
install -m 0644 "$REPO/deploy/nginx-editor-env.conf" /etc/nginx/conf.d/editor-env.conf
echo "  installed /etc/nginx/conf.d/editor-env.conf (defaults: feature OFF)"

echo
echo "=== nginx site config ==="
backup /etc/nginx/sites-enabled/levels
install -m 0644 "$REPO/conf/levels.nginx" /etc/nginx/sites-enabled/levels
echo "  installed /etc/nginx/sites-enabled/levels"

echo
echo "=== nginx syntax check ==="
if ! nginx -t; then
    echo "ABORT: nginx -t failed. Restoring backups." >&2
    for f in /etc/nginx/conf.d/ratelimit.conf \
             /etc/nginx/conf.d/editor-env.conf \
             /etc/nginx/sites-enabled/levels; do
        if [[ -e $f.bak.$STAMP ]]; then
            mv "$f.bak.$STAMP" "$f"
            echo "  restored $f"
        fi
    done
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
       map $host $hcaptcha_site_key  { default "<your hcaptcha site key>"; }
       map $host $hcaptcha_secret    { default "<your hcaptcha secret>"; }
       map $host $mail_from          { default "noreply@levels.wkcc.org"; }

     then:  sudo nginx -t && sudo systemctl reload nginx

  Installed file backups (if any) are tagged with the current UTC timestamp.

EOF
echo "Install complete."
