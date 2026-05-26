#!/usr/bin/env bash
# install-config.sh — install kayak's deploy-time config infrastructure:
#   1. /etc/kayak/env (mode 0644 root:root) — non-secret path indirection
#      (KAYAK_HOME) read by every kayak-*.service via EnvironmentFile=.
#   2. /etc/kayak/secrets.env (mode 0600 root:www-data) — PHP-FPM secrets.
#   3. The PHP-FPM pool overlay + systemd drop-in that wires the
#      secrets.env into the php-fpm master.
#
# Idempotent: safe to re-run. Won't overwrite an existing /etc/kayak/env
# or /etc/kayak/secrets.env (the env file's idempotence preserves any
# operator hand-edits; the secrets file preserves the live values).
#
# Run on a fresh host (or after re-imaging). After the first run it
# installs the env file (KAYAK_HOME=/home/pat default) and the secrets
# template, then exits so you can edit secrets.env; re-run after.
#
# Usage:
#   sudo deploy/install-config.sh
#
# Run from the repo root. Must be root (invoke via sudo).
#
# History: this was originally install-secrets.sh (and before that,
# migrate-secrets.sh — a one-shot to move HCAPTCHA_SECRET off nginx
# fastcgi_param). Renamed to install-config.sh in T3.3 Phase 5.2 when
# the /etc/kayak/env step landed; the captcha provider switched from
# hCaptcha to Cloudflare Turnstile on 2026-05-01.

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
ENV_FILE="/etc/kayak/env"

say() { printf '• %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 0. KAYAK_HOME env file — non-secret path indirection (T3.3 Phase 5).
#    Installs ahead of secrets so the rest of the script can rely on
#    /etc/kayak/ existing. mode 0644 root:root — world-readable on
#    purpose; this is a deploy-time path, not a secret.
# ---------------------------------------------------------------------------

if [[ -e "$ENV_FILE" ]]; then
    say "kayak env file already present at $ENV_FILE (leaving untouched)"
else
    say "installing kayak env file at $ENV_FILE"
    install -D -m 0644 -o root -g root \
        "$DEPLOY_DIR/kayak-env.example" \
        "$ENV_FILE"
fi

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
# 2.5. Dummy TLS cert for the default (bare-IP) vhost.
#    deploy/nginx-default-server listens 443 ssl and references
#    /etc/nginx/ssl/dummy.{crt,key} to complete the TLS handshake before
#    returning 444. Without them a fresh `nginx -t` fails.
# ---------------------------------------------------------------------------

if [[ ! -f /etc/nginx/ssl/dummy.crt ]]; then
    say "generating self-signed dummy cert for the default vhost"
    install -d -m 0755 /etc/nginx/ssl
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -subj "/CN=invalid" \
        -keyout /etc/nginx/ssl/dummy.key \
        -out /etc/nginx/ssl/dummy.crt
    chmod 600 /etc/nginx/ssl/dummy.key
fi

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
# `^[[:space:]]*#` strips comments — nginx -T renders comments with the
# location-block's leading indent, so the prior bare `^#` filter let
# explanatory comments through (e.g. levels-common.conf's
# "# getenv() fallback for TURNSTILE_SECRET via the FPM-pool env channel.").
# Also pin to a leading `fastcgi_param ` directive: a bare secret-name
# match anywhere in a line otherwise picks up any prose mentioning the
# token, even in `add_header` or comment text.
if nginx -T 2>/dev/null \
    | grep -v '^[[:space:]]*#' \
    | grep -Ei '^[[:space:]]*fastcgi_param[[:space:]]+(HCAPTCHA|TURNSTILE)_SECRET[[:space:]]+[^$]'; then
    echo "warning: nginx still references the secret directly — inspect above" >&2
    exit 4
fi

echo ""
echo "✓ install complete."
echo "  secrets live only in $SECRETS_FILE (mode 0600 root:www-data)"
echo "  $FPM_UNIT and nginx reloaded"
