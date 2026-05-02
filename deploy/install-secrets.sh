#!/usr/bin/env bash
# install-secrets.sh — install the PHP-FPM secrets infrastructure for the
# kayak app: /etc/kayak/secrets.env (mode 0600 root:www-data), the kayak
# pool overlay, and the systemd drop-in that wires the env file into the
# php-fpm master.
#
# Idempotent: safe to re-run. Won't overwrite an existing secrets.env.
#
# Run on a fresh host (or after re-imaging) to bring the secrets-handling
# infrastructure up. After the first run it installs the secrets.env
# template and exits — edit /etc/kayak/secrets.env, then re-run.
#
# Usage:
#   sudo deploy/install-secrets.sh
#
# Run from the repo root. Must be root (invoke via sudo).
#
# History: this was originally migrate-secrets.sh, a one-shot migration
# that moved HCAPTCHA_SECRET off nginx fastcgi_param. The migration is
# done; the install part remains useful for fresh deploys. The captcha
# provider switched from hCaptcha to Cloudflare Turnstile on 2026-05-01;
# the only key in secrets.env today is TURNSTILE_SECRET.

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
if [[ -z "$(grep -E '^TURNSTILE_SECRET=' "$SECRETS_FILE" | head -1 | cut -d= -f2-)" ]]; then
    echo "error: $SECRETS_FILE is missing a value for TURNSTILE_SECRET" >&2
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
# 3. Validate nginx + reload services.
# ---------------------------------------------------------------------------

say "validating nginx config"
nginx -t

say "restarting $FPM_UNIT"
systemctl restart "$FPM_UNIT"

say "reloading nginx"
systemctl reload nginx

# ---------------------------------------------------------------------------
# 4. Post-flight check.
# ---------------------------------------------------------------------------

say "post-flight: scanning nginx -T for any remaining plaintext captcha secret"
if nginx -T 2>/dev/null | grep -Ei '(HCAPTCHA|TURNSTILE)_SECRET[[:space:]]+[^$]' | grep -v '^#'; then
    echo "warning: nginx still references the secret directly — inspect above" >&2
    exit 4
fi

echo ""
echo "✓ install complete."
echo "  secrets live only in $SECRETS_FILE (mode 0600 root:www-data)"
echo "  $FPM_UNIT and nginx reloaded"
