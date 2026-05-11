#!/usr/bin/env bash
# scripts/sysinfo-for-review.sh
#
# Read-only system info dump for the pre-cutover review. Run from any
# directory. Outputs to /tmp/kayak-sysinfo-<TIMESTAMP>.txt and prints
# that path at the end so it can be pasted back into the review.
#
# Some commands need sudo (nginx -T, ss -p, fail2ban-client, certbot,
# nft, journalctl on root-owned units, /var/log/auth.log, mailq). The
# script calls `sudo -v` up front to prompt once.
#
# Nothing in here writes to the system. The script does NOT change
# state -- diffs are read-only, journalctl is `--no-pager -n N`, no
# `apt update` (just `apt list --upgradable` which reads the cache).
#
# Usage:  bash scripts/sysinfo-for-review.sh
#         or  ./scripts/sysinfo-for-review.sh

set -uo pipefail

REPO=/home/pat/kayak
TS=$(date +%Y%m%d-%H%M%S)
OUT=/tmp/kayak-sysinfo-${TS}.txt

# All output goes to both stdout and the file.
exec > >(tee "$OUT") 2>&1

banner() { printf '\n\n========== %s ==========\n' "$*"; }
section() { printf '\n----- %s -----\n' "$*"; }
run()  { printf '\n$ %s\n' "$*"; eval "$*" 2>&1 || true; }
runS() { printf '\n$ sudo %s\n' "$*"; sudo bash -c "$*" 2>&1 || true; }

# Warm sudo once; the rest of the script reuses the cached credentials.
echo "kayak sysinfo dump"
echo "time: $(date -Iseconds)"
echo "host: $(hostname)"
echo "user: $(whoami)"
echo "repo: $REPO"
echo "out:  $OUT"
echo
echo "(prompting for sudo once now; nothing destructive will run)"
sudo -v


banner "1. Identity + capacity"
run uname -a
run uptime
run who -b
run df -h
run free -h
run "top -bn1 | head -25"


banner "2. Network exposure"
runS "ss -tlnp"
runS "ss -ulnp | head -30"
section "firewall"
runS "nft list ruleset 2>/dev/null || ufw status verbose 2>/dev/null || iptables -L -n -v"
section "DNS A/AAAA"
for host in levels.wkcc.org levels-test.wkcc.org levels.mousebrains.com wkcc.org; do
  run "dig +short A    $host"
  run "dig +short AAAA $host"
done
section "DNS MX/TXT (mail + DKIM/SPF/DMARC)"
for d in wkcc.org mousebrains.com; do
  run "dig +short MX  $d"
  run "dig +short TXT $d"
  run "dig +short TXT _dmarc.$d"
done
section "CAA (cert issuer pinning)"
for d in wkcc.org mousebrains.com; do
  run "dig +short CAA $d"
done


banner "3. Nginx"
runS "nginx -t"
runS "nginx -T"
run "systemctl status nginx --no-pager"
section "nginx error log (last 100)"
runS "tail -n 100 /var/log/nginx/error.log"
runS "tail -n 100 /var/log/nginx/kayak-error.log"


banner "4. PHP-FPM"
run "systemctl status php8.4-fpm --no-pager"
runS "php-fpm8.4 -tt"
section "active pools"
runS "ls -la /etc/php/8.4/fpm/pool.d/"
section "fpm slow log if any"
runS "ls -la /var/log/php* 2>/dev/null"


banner "5. Kayak systemd units"
UNITS=(
  kayak-pipeline.service
  kayak-pipeline.timer
  kayak-decimate.service
  kayak-decimate.timer
  kayak-backup.service
  kayak-backup.timer
  kayak-backup-offsite.service
  kayak-heartbeat.service
  kayak-heartbeat.timer
  kayak-healthcheck.service
  kayak-healthcheck.timer
  kayak-audit-gauges.service
  kayak-audit-gauges.timer
  kayak-notify-failure@.service
)
for u in "${UNITS[@]}"; do
  section "unit: $u"
  run "systemctl status $u --no-pager"
done
section "all kayak timers"
run "systemctl list-timers 'kayak-*' --all --no-pager"


banner "6. Recent kayak unit logs (--no-pager -n 200)"
for u in kayak-pipeline kayak-decimate kayak-backup kayak-backup-offsite \
         kayak-heartbeat kayak-healthcheck kayak-audit-gauges; do
  section "journal: $u"
  runS "journalctl --no-pager -n 200 -u $u.service"
done


banner "7. fail2ban"
runS "fail2ban-client status"
runS "fail2ban-client status | awk -F'\t' '/Jail list:/ {print \$2}' | tr ',' '\\n' | sed 's/^ *//' | while read j; do
  [ -n \"\$j\" ] && echo \"--- jail: \$j ---\" && fail2ban-client status \"\$j\"
done"


banner "8. TLS / certs"
runS "certbot certificates"
section "live cert chain (levels-test)"
run "openssl s_client -connect levels-test.wkcc.org:443 -servername levels-test.wkcc.org </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName"
section "live cert chain (levels.mousebrains.com)"
run "openssl s_client -connect levels.mousebrains.com:443 -servername levels.mousebrains.com </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName"


banner "9. Mail"
runS "mailq"
runS "tail -n 100 /var/log/mail.log"
run "test -f /home/pat/.msmtprc && cat /home/pat/.msmtprc | sed 's/password.*/password REDACTED/'"
section "msmtp smoke (no message sent — TLS handshake only)"
run "msmtp --serverinfo --host=smtp.gmail.com --port=587 --tls --tls-starttls 2>&1 | head -40"


banner "10. File-perm spot checks"
runS "ls -la /etc/kayak/ 2>/dev/null"
runS "ls -la /etc/kayak/secrets.env 2>/dev/null"
runS "ls -la /etc/nginx/conf.d/editor-env.conf"
runS "ls -la /etc/php/8.4/fpm/pool.d/"
runS "ls -la /etc/fail2ban/jail.local /etc/fail2ban/jail.d/ /etc/fail2ban/filter.d/kayak* /etc/fail2ban/filter.d/nginx-edit* /etc/fail2ban/filter.d/nginx-mal* /etc/fail2ban/filter.d/nginx-default-block.conf"
runS "ls -la /etc/ssh/sshd_config.d/"
runS "ls -la /etc/systemd/system/kayak-* /etc/systemd/system/multi-user.target.wants/kayak-* 2>/dev/null"
runS "ls -la /etc/nginx/snippets/security-headers*.conf"


banner "11. Drift detection: repo vs /etc"
diff_pair() {
  local repo="$1"
  local live="$2"
  printf '\n--- %s  <->  %s ---\n' "$repo" "$live"
  if [ ! -f "$repo" ]; then echo "(repo missing)"; return; fi
  if ! sudo test -f "$live"; then echo "(live missing)"; return; fi
  sudo diff -u "$repo" "$live" 2>&1 | head -80 || true
  # If diff produced no output, it printed nothing -- emit OK marker.
  sudo diff -q "$repo" "$live" >/dev/null 2>&1 && echo "(identical)"
}
# nginx
diff_pair "$REPO/conf/security-headers.conf"            /etc/nginx/snippets/security-headers.conf
diff_pair "$REPO/conf/security-headers-turnstile.conf"  /etc/nginx/snippets/security-headers-turnstile.conf
diff_pair "$REPO/deploy/levels"                          /etc/nginx/sites-available/levels
diff_pair "$REPO/deploy/nginx-default-server"            /etc/nginx/conf.d/default-server.conf
diff_pair "$REPO/deploy/nginx-editor-env.conf"           /etc/nginx/conf.d/editor-env.conf
diff_pair "$REPO/deploy/nginx-kayak-log-format.conf"     /etc/nginx/conf.d/nginx-kayak-log-format.conf
diff_pair "$REPO/deploy/nginx-ratelimit.conf"            /etc/nginx/conf.d/nginx-ratelimit.conf
# php-fpm
diff_pair "$REPO/deploy/kayak-fpm-pool.conf"             /etc/php/8.4/fpm/pool.d/kayak.conf
# logrotate
diff_pair "$REPO/deploy/logrotate-kayak-csp"             /etc/logrotate.d/kayak-csp
# fail2ban
diff_pair "$REPO/deploy/fail2ban/jail.local"             /etc/fail2ban/jail.local
diff_pair "$REPO/deploy/fail2ban/jail.d/kayak-edit.conf" /etc/fail2ban/jail.d/kayak-edit.conf
diff_pair "$REPO/deploy/fail2ban/jail.d/kayak-editor-auth.conf" /etc/fail2ban/jail.d/kayak-editor-auth.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-edit-auth.conf"     /etc/fail2ban/filter.d/nginx-edit-auth.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-editor-auth.conf"   /etc/fail2ban/filter.d/nginx-editor-auth.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-malicious.conf"     /etc/fail2ban/filter.d/nginx-malicious.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-default-block.conf" /etc/fail2ban/filter.d/nginx-default-block.conf
# sshd
diff_pair "$REPO/deploy/sshd_config.d/hardening.conf"   /etc/ssh/sshd_config.d/hardening.conf
# systemd
for unit in kayak-pipeline.service kayak-pipeline.timer \
            kayak-decimate.service kayak-decimate.timer \
            kayak-backup.service kayak-backup.timer kayak-backup.sh \
            kayak-backup-offsite.service kayak-backup-offsite.sh \
            kayak-heartbeat.service kayak-heartbeat.timer kayak-heartbeat.sh \
            kayak-healthcheck.service kayak-healthcheck.timer \
            kayak-audit-gauges.service kayak-audit-gauges.timer \
            kayak-notify-failure@.service ; do
  diff_pair "$REPO/systemd/$unit" "/etc/systemd/system/$unit"
done


banner "12. Pending OS updates"
run "apt list --upgradable 2>/dev/null | head -100"
runS "unattended-upgrade --dry-run -v 2>&1 | tail -40"
section "package counts"
run "dpkg -l | grep -c '^ii'"


banner "13. Recent sshd auth activity"
runS "journalctl _COMM=sshd --no-pager -n 200"
runS "tail -n 100 /var/log/auth.log"


banner "14. CSP report tail"
run "test -f /home/pat/logs/csp.log && tail -n 100 /home/pat/logs/csp.log"


banner "15. Disk + DB footprint"
run "du -sh /home/pat/DB /home/pat/public_html /home/pat/backups /home/pat/logs 2>/dev/null"
runS "du -sh /var/log /var/cache /var/lib 2>/dev/null"
section "DB size + WAL state"
run "ls -lah /home/pat/DB/"
run "sqlite3 /home/pat/DB/kayak.db 'PRAGMA journal_mode; PRAGMA integrity_check; PRAGMA quick_check; PRAGMA wal_checkpoint;' 2>&1"
section "newest backup"
run "ls -lht /home/pat/backups/ 2>/dev/null | head -5"


banner "16. crontab + at queue (should be empty -- everything is systemd)"
run "crontab -l 2>/dev/null"
runS "ls /etc/cron.d/ /etc/cron.hourly/ /etc/cron.daily/ /etc/cron.weekly/ 2>/dev/null"
runS "atq 2>/dev/null"


banner "done"
echo
echo "wrote: $OUT"
echo
echo "size: $(wc -c < "$OUT") bytes"
echo
echo "next: paste the path or upload the file in the review chat."
