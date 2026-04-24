#!/usr/bin/env bash
#
# install-observability.sh — install the nginx timed-log-format drop-in,
# the CSP violation report sink, and the logrotate config for the CSP log.
#
# Enables two features used by ../logs/analyze.py:
#   - per-route latency quantiles (from rt=$request_time)
#   - CSP violation diffs (from /home/pat/logs/csp.log)
#
# Does NOT touch /etc/nginx/sites-enabled/levels except to rewrite a single
# access_log line. The full target-state vhost lands via
# install-editor-feature.sh (which cp's conf/levels.nginx end-to-end).
#
# Run as root:  sudo bash scripts/install-observability.sh
#
# Safe to re-run. Backups of replaced files go under
#   /var/backups/kayak-nginx/<UTC timestamp>/
# (outside nginx's include paths so stray .bak files don't break parsing).

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

NEW_FILES=()
REPLACED_FILES=()

backup_replace() {
    local dest=$1 src=$2
    if [[ -e $dest ]]; then
        cp -a "$dest" "$BACKUP_DIR/$(basename "$dest")"
        REPLACED_FILES+=("$dest")
        echo "  backed up $dest"
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
        [[ -e $bak ]] && install -m 0644 "$bak" "$f" && echo "  restored $f"
    done
    for f in "${NEW_FILES[@]-}"; do
        [[ -n ${f:-} ]] || continue
        rm -f "$f" && echo "  removed $f"
    done
}

echo "=== nginx log_format drop-in ==="
backup_replace /etc/nginx/conf.d/kayak-log-format.conf \
               "$REPO/deploy/nginx-kayak-log-format.conf"

echo
echo "=== nginx access_log directive on kayak vhost ==="
VHOST=/etc/nginx/sites-available/levels
if grep -qE '^\s*access_log /var/log/nginx/kayak-access\.log kayak_timed;' "$VHOST"; then
    echo "  already uses kayak_timed — no change"
elif grep -qE '^\s*access_log /var/log/nginx/kayak-access\.log;' "$VHOST"; then
    cp -a "$VHOST" "$BACKUP_DIR/$(basename "$VHOST")"
    REPLACED_FILES+=("$VHOST")
    sed -i 's|^\(\s*\)access_log /var/log/nginx/kayak-access\.log;|\1access_log /var/log/nginx/kayak-access.log kayak_timed;|' "$VHOST"
    echo "  rewrote access_log line (backup in $BACKUP_DIR)"
else
    echo "  WARNING: expected access_log line not found in $VHOST — skipping"
fi

echo
echo "=== CSP security-headers snippets (with report-uri) ==="
mkdir -p /etc/nginx/snippets
backup_replace /etc/nginx/snippets/security-headers.conf \
               "$REPO/conf/security-headers.conf"
backup_replace /etc/nginx/snippets/security-headers-hcaptcha.conf \
               "$REPO/conf/security-headers-hcaptcha.conf"

echo
echo "=== logrotate for the CSP log ==="
backup_replace /etc/logrotate.d/kayak-csp "$REPO/deploy/logrotate-kayak-csp"
if ! logrotate -d /etc/logrotate.d/kayak-csp >/dev/null 2>&1; then
    echo "  WARNING: logrotate -d reported an error for kayak-csp — check manually:"
    echo "    logrotate -d /etc/logrotate.d/kayak-csp"
fi

echo
echo "=== ACLs for /home/pat/logs (CSP log home) ==="
# PHP writes /home/pat/logs/csp.log as www-data. The access ACL grants
# write on the directory itself; the default ACL ensures that whatever
# logrotate (or the first request after a rotate) creates inherits the
# right permissions so both www-data (writes) and pat (reads) can work.
LOGS_DIR=/home/pat/logs
if [[ ! -d $LOGS_DIR ]]; then
    install -d -o pat -g pat -m 0755 "$LOGS_DIR"
    echo "  created $LOGS_DIR"
fi
need_access=1
if getfacl "$LOGS_DIR" 2>/dev/null | grep -qE '^user:www-data:.*w'; then
    need_access=0
fi
if (( need_access )); then
    setfacl -m u:www-data:rwx "$LOGS_DIR"
    echo "  added access ACL: u:www-data:rwx on $LOGS_DIR"
else
    echo "  access ACL for www-data already present"
fi
if getfacl "$LOGS_DIR" 2>/dev/null | grep -qE '^default:user:www-data:.*w'; then
    echo "  default ACL for www-data already present"
else
    setfacl -d -m u:www-data:rwx "$LOGS_DIR"
    echo "  added default ACL: u:www-data:rwx on $LOGS_DIR"
fi
if getfacl "$LOGS_DIR" 2>/dev/null | grep -qE '^default:user:pat:.*w'; then
    echo "  default ACL for pat already present"
else
    setfacl -d -m u:pat:rwx "$LOGS_DIR"
    echo "  added default ACL: u:pat:rwx on $LOGS_DIR"
fi

echo
echo "=== nginx syntax check ==="
if ! nginx -t; then
    rollback
    exit 1
fi

echo
echo "=== reload nginx ==="
systemctl reload nginx
echo "  nginx reloaded"

echo
echo "=== smoke tests ==="
# systemctl reload returns as soon as the SIGHUP is sent; old workers keep
# serving in-flight requests under the previous config until they drain.
# A short pause makes the first curl land on a new worker running the
# freshly installed log_format.
sleep 1
# 1. Timed log format — any request lands an rt=/urt= tail.
curl -fsS -o /dev/null https://levels.mousebrains.com/ || true
last_line=$(tail -n 1 /var/log/nginx/kayak-access.log 2>/dev/null || true)
if [[ -n ${last_line:-} && $last_line == *" rt="*" urt="* ]]; then
    echo "  OK: kayak-access.log tail carries rt=/urt="
else
    echo "  WARNING: latest kayak-access.log line is missing rt=/urt= fields"
    echo "    $last_line"
fi

# 2. CSP sink — synthetic POST yields 204 and a csp.log line.
resp=$(curl -s -o /dev/null -w '%{http_code}' \
    -XPOST -H 'Content-Type: application/csp-report' \
    --data '{"csp-report":{"document-uri":"https://levels.mousebrains.com/","violated-directive":"installer-smoke","blocked-uri":"https://example.invalid/"}}' \
    https://levels.mousebrains.com/csp-report.php || echo "000")
if [[ $resp == "204" ]]; then
    echo "  OK: /csp-report.php returned 204"
    # Give the FPM worker a moment to flush the append.
    sleep 1
    if tail -n 1 /home/pat/logs/csp.log 2>/dev/null | grep -q installer-smoke; then
        echo "  OK: /home/pat/logs/csp.log captured the synthetic report"
    else
        echo "  WARNING: synthetic report did not land in /home/pat/logs/csp.log"
        echo "    check kayak-error.log for open_basedir / permissions errors"
    fi
else
    echo "  WARNING: /csp-report.php smoke test returned HTTP $resp (expected 204)"
    echo "    /csp-report.php may not be deployed yet — run 'levels pipeline'"
fi

echo
echo "Install complete."
