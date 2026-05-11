#!/usr/bin/env bash
# Apply the favicon location block from conf/levels.nginx to live nginx.
#
# After commit X (favicon block added to conf/levels.nginx), prod's
# /etc/nginx/sites-available/levels still lacks the block. Run this to
# diff, copy, test, and reload.
#
# Usage:
#   sudo bash scripts/apply-nginx-favicon.sh
#
# Requires sudo for the cp/test/reload steps.

set -euo pipefail

REPO_CONF="/home/pat/kayak/conf/levels.nginx"
LIVE_CONF="/etc/nginx/sites-available/levels"

if [[ ! -r "$REPO_CONF" ]]; then
    echo "ERROR: $REPO_CONF not readable" >&2
    exit 1
fi

echo "=== diff (repo - live) ==="
if diff "$REPO_CONF" "$LIVE_CONF"; then
    echo "(no diff — nothing to apply, exiting)"
    exit 0
fi
echo ""

read -r -p "Apply the diff above to $LIVE_CONF and reload nginx? [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "aborted"
    exit 1
fi

cp "$REPO_CONF" "$LIVE_CONF"
echo "copied → $LIVE_CONF"

nginx -t
systemctl reload nginx
echo "nginx reloaded"

echo ""
echo "=== verify favicon now resolves ==="
curl -sIo /dev/null -w "/favicon.ico:        %{http_code}\n" https://levels.mousebrains.com/favicon.ico
curl -sIo /dev/null -w "/static/favicon.ico: %{http_code}\n" https://levels.mousebrains.com/static/favicon.ico
