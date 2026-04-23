#!/usr/bin/env bash
# migrate-secrets.sh — move HCAPTCHA_SECRET off nginx fastcgi_param into
# /etc/kayak/secrets.env (mode 0600 root:www-data), exposed to PHP via a
# PHP-FPM pool env[] overlay + systemd drop-in. Also cleans up the now-
# obsolete /etc/nginx/snippets/edit-password.conf and the site-file
# include that referenced it — edit.php moved to editor-cookie auth, so
# EDIT_PASSWORD is no longer read anywhere.
#
# Idempotent: safe to re-run. Won't overwrite an existing secrets.env.
#
# Usage:
#   sudo deploy/migrate-secrets.sh
#
# Run from the repo root. Must be root (invoke via sudo).

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "error: must run as root (use sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="$REPO_DIR/deploy"

# Detect PHP major.minor so we place files under the right path.
PHP_VER="$(php -r 'echo PHP_MAJOR_VERSION . "." . PHP_MINOR_VERSION;')"
FPM_UNIT="php${PHP_VER}-fpm.service"
FPM_POOL_DIR="/etc/php/${PHP_VER}/fpm/pool.d"
FPM_DROPIN_DIR="/etc/systemd/system/${FPM_UNIT}.d"

NGINX_SITE="/etc/nginx/sites-available/levels"
NGINX_ENV="/etc/nginx/conf.d/editor-env.conf"
NGINX_EDIT_PWD_SNIPPET="/etc/nginx/snippets/edit-password.conf"
SECRETS_FILE="/etc/kayak/secrets.env"

say() { printf '• %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. Secrets env file — install only if missing so we don't clobber live values.
# ---------------------------------------------------------------------------

if [[ -e "$SECRETS_FILE" ]]; then
    say "secrets.env already present at $SECRETS_FILE (leaving untouched)"
else
    say "installing secrets.env template at $SECRETS_FILE"
    install -D -m 0600 -o root -g www-data \
        "$DEPLOY_DIR/secrets.env.example" \
        "$SECRETS_FILE"
    echo ""
    echo "  >>> EDIT $SECRETS_FILE NOW and re-run this script. <<<"
    echo "  Suggested: sudo -e $SECRETS_FILE"
    exit 2
fi

# Sanity: required value present (non-empty) in the secrets file.
if [[ -z "$(grep -E '^HCAPTCHA_SECRET=' "$SECRETS_FILE" | head -1 | cut -d= -f2-)" ]]; then
    echo "error: $SECRETS_FILE is missing a value for HCAPTCHA_SECRET" >&2
    echo "edit it with: sudo -e $SECRETS_FILE" >&2
    exit 3
fi

# ---------------------------------------------------------------------------
# 2. PHP-FPM pool overlay + systemd drop-in.
# ---------------------------------------------------------------------------

say "installing pool overlay at $FPM_POOL_DIR/kayak.conf"
install -D -m 0644 "$DEPLOY_DIR/kayak-fpm-pool.conf" "$FPM_POOL_DIR/kayak.conf"

say "installing systemd drop-in at $FPM_DROPIN_DIR/kayak-secrets.conf"
install -D -m 0644 "$DEPLOY_DIR/php-fpm-secrets-dropin.conf" \
    "$FPM_DROPIN_DIR/kayak-secrets.conf"

say "reloading systemd"
systemctl daemon-reload

# ---------------------------------------------------------------------------
# 3. Strip secret lines from nginx site config.
# ---------------------------------------------------------------------------

if [[ -f "$NGINX_SITE" ]]; then
    say "removing HCAPTCHA_SECRET fastcgi_param lines from $NGINX_SITE"
    sed -i '/fastcgi_param HCAPTCHA_SECRET/d' "$NGINX_SITE"

    say "removing edit-password.conf include from $NGINX_SITE"
    sed -i '/include .*edit-password\.conf/d' "$NGINX_SITE"
else
    say "nginx site file not found at $NGINX_SITE — skipping (adjust path if deployed elsewhere)"
fi

if [[ -f "$NGINX_ENV" ]]; then
    say "commenting map \$hcaptcha_secret in $NGINX_ENV"
    # Only comment if it's not already commented (idempotence).
    sed -i -E 's|^([[:space:]]*map[[:space:]]+\$host[[:space:]]+\$hcaptcha_secret)|# \1|' "$NGINX_ENV"
fi

if [[ -f "$NGINX_EDIT_PWD_SNIPPET" ]]; then
    say "removing obsolete $NGINX_EDIT_PWD_SNIPPET (values now in $SECRETS_FILE)"
    rm -f "$NGINX_EDIT_PWD_SNIPPET"
fi

# ---------------------------------------------------------------------------
# 4. Validate nginx + reload services.
# ---------------------------------------------------------------------------

say "validating nginx config"
nginx -t

say "restarting $FPM_UNIT"
systemctl restart "$FPM_UNIT"

say "reloading nginx"
systemctl reload nginx

# ---------------------------------------------------------------------------
# 5. Post-flight check.
# ---------------------------------------------------------------------------

say "post-flight: scanning nginx -T for any remaining plaintext HCAPTCHA_SECRET"
if nginx -T 2>/dev/null | grep -Ei 'HCAPTCHA_SECRET[[:space:]]+[^$]' | grep -v '^#'; then
    echo "warning: nginx still references the secret directly — inspect above" >&2
    exit 4
fi

echo ""
echo "✓ migration complete."
echo "  secrets live only in $SECRETS_FILE (mode 0600 root:www-data)"
echo "  $FPM_UNIT and nginx reloaded"
