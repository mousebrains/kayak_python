#!/usr/bin/env bash
# scripts/audit-t30.sh
#
# Read-only deep audit dump for the T0..T+30 review window (DNS cutover
# 2026-05-20 → stabilization through ~2026-06-19). Builds on the
# preflight-2026-05-10 sysinfo script: same shape, expanded systemd
# unit list, plus cutover- and Cloudflare-specific probes.
#
# Outputs to /tmp/kayak-audit-<TIMESTAMP>.txt and prints the path at
# the end. Some sections need sudo (nginx -T, ss -p, fail2ban-client,
# certbot, nft, journalctl on root-owned units, /var/log/auth.log,
# mailq, /etc/kayak/secrets.env stat). The script does `sudo -v` up
# front so you're prompted once.
#
# Nothing changes state — diffs are read-only, journalctl is
# `--no-pager -n N`, no `apt update`.
#
# Usage:  bash scripts/audit-t30.sh
#         or  ./scripts/audit-t30.sh

set -uo pipefail

REPO=/home/pat/kayak
TS=$(date +%Y%m%d-%H%M%S)
OUT=/tmp/kayak-audit-${TS}.txt

exec > >(tee "$OUT") 2>&1

banner() { printf '\n\n========== %s ==========\n' "$*"; }
section() { printf '\n----- %s -----\n' "$*"; }
run()  { printf '\n$ %s\n' "$*"; eval "$*" 2>&1 || true; }
runS() { printf '\n$ sudo %s\n' "$*"; sudo bash -c "$*" 2>&1 || true; }

echo "kayak T+30 audit dump"
echo "time:  $(date -Iseconds)"
echo "host:  $(hostname)"
echo "user:  $(whoami)"
echo "repo:  $REPO"
echo "out:   $OUT"
echo
echo "(prompting for sudo once now; nothing destructive will run)"
sudo -v


banner "1. Identity + capacity"
run uname -a
run uptime
run who -b
run "df -h | grep -vE '^(tmpfs|udev|overlay)'"
run "df -i | grep -vE '^(tmpfs|udev|overlay)'"
run free -h
run "/sbin/swapon --show 2>/dev/null || cat /proc/swaps"
run "top -bn1 | head -25"


banner "2. Network exposure"
runS "ss -tlnp"
runS "ss -ulnp | head -30"
section "firewall"
runS "nft list ruleset 2>/dev/null || ufw status verbose 2>/dev/null || iptables -L -n -v"
section "DNS A/AAAA/CNAME (resolvers: 1.1.1.1, 8.8.8.8, 9.9.9.9, 208.67.222.222)"
for host in levels.wkcc.org www.levels.wkcc.org levels-test.wkcc.org \
            levels.mousebrains.com www.levels.mousebrains.com wkcc.org mousebrains.com; do
  section "host: $host"
  for r in 1.1.1.1 8.8.8.8 9.9.9.9 208.67.222.222; do
    printf '\n@%s:\n' "$r"
    dig +short @"$r" CNAME "$host" 2>/dev/null | sed 's/^/  CNAME /' || true
    dig +short @"$r" A     "$host" 2>/dev/null | sed 's/^/  A     /' || true
    dig +short @"$r" AAAA  "$host" 2>/dev/null | sed 's/^/  AAAA  /' || true
  done
done
section "DNS MX/TXT (mail + DKIM/SPF/DMARC)"
for d in wkcc.org mousebrains.com; do
  run "dig +short MX  $d"
  run "dig +short TXT $d"
  run "dig +short TXT _dmarc.$d"
done
section "CAA (cert issuer pinning)"
for d in wkcc.org mousebrains.com levels.mousebrains.com levels.wkcc.org; do
  run "dig +short CAA $d"
done
section "DNSSEC chain (mousebrains.com)"
run "dig +dnssec +short mousebrains.com SOA"
run "dig +short mousebrains.com DS"
section "Cloudflare proxy state — proxied A records return CF anycast (104.16/17/19.0.0/13 etc.)"
section "Check: is levels.mousebrains.com DNS-only (5.78.185.66) or proxied (CF range)?"
run "dig +short A levels.mousebrains.com @1.1.1.1"
run "dig +short A levels.wkcc.org @1.1.1.1"
section "NS records for the two apexes"
run "dig +short NS mousebrains.com @1.1.1.1"
run "dig +short NS wkcc.org @1.1.1.1"


banner "3. Nginx"
runS "nginx -t"
section "sites-enabled list (should be: levels-mousebrains-com, levels-test-wkcc-org, levels-wkcc-org)"
runS "ls -la /etc/nginx/sites-enabled/"
section "nginx -T (full config — long; grep for 'server_name', 'ssl_certificate', 'listen' for the highlights)"
runS "nginx -T 2>&1 | head -1200"
run "systemctl status nginx --no-pager"
section "nginx error log (last 100, suppressing routine handshake-abort noise)"
runS "tail -n 100 /var/log/nginx/error.log"
section "per-vhost error logs (last 80 each)"
for f in /var/log/nginx/levels-mousebrains.error.log /var/log/nginx/levels-test.error.log /var/log/nginx/levels-wkcc.error.log; do
  runS "test -f $f && (echo '== '$f' =='; tail -n 80 $f) || echo 'missing: $f'"
done


banner "4. PHP-FPM"
run "systemctl status php8.4-fpm --no-pager"
runS "php-fpm8.4 -tt 2>&1 | head -60"
section "active pools"
runS "ls -la /etc/php/8.4/fpm/pool.d/"
section "fpm slow log if any"
runS "ls -la /var/log/php* 2>/dev/null"
section "fpm pool resource limits + open_basedir"
runS "grep -E '^(pm\\.|listen|user|group|request_terminate_timeout|open_basedir|catch_workers_output|chdir)' /etc/php/8.4/fpm/pool.d/*.conf"


banner "5. Kayak systemd units"
UNITS=(
  kayak-audit-gauges.service       kayak-audit-gauges.timer
  kayak-backup-hourly.service      kayak-backup-hourly.timer
  kayak-backup-weekly.service      kayak-backup-weekly.timer
  kayak-backup-offsite.service
  kayak-cert-expiry.service        kayak-cert-expiry.timer
  kayak-cert-renewal-test.service  kayak-cert-renewal-test.timer
  kayak-config-drift.service       kayak-config-drift.timer
  kayak-decimate.service           kayak-decimate.timer
  kayak-editor-retention.service   kayak-editor-retention.timer
  kayak-fail-test.service
  kayak-healthcheck.service        kayak-healthcheck.timer
  kayak-heartbeat.service          kayak-heartbeat.timer
  kayak-metadata-snapshot.service  kayak-metadata-snapshot.timer
  kayak-notify-failure@.service
  kayak-pipeline.service           kayak-pipeline.timer
  kayak-recap.service              kayak-recap.timer
)
for u in "${UNITS[@]}"; do
  section "unit: $u"
  # kayak-notify-failure@.service is a template — `systemctl status` on the
  # unparameterized name fails with "neither a valid invocation ID nor unit
  # name". Skip it here; section 6 shows journal output for `@*.service`
  # instances instead.
  if [ "$u" = "kayak-notify-failure@.service" ]; then
    echo "(template service — not addressable; see section 6 journal sweep)"
    continue
  fi
  run "systemctl status $u --no-pager 2>&1 | head -20"
done
section "all kayak timers (next-run + last-run)"
run "systemctl list-timers 'kayak-*' --all --no-pager"
section "system-wide failed units (anything red?)"
run "systemctl list-units --state=failed --no-pager"


banner "6. Recent kayak unit logs (--no-pager -n 100 each, OnFailure-instances rolled up)"
for u in kayak-pipeline kayak-decimate kayak-backup-hourly kayak-backup-weekly \
         kayak-backup-offsite kayak-heartbeat kayak-healthcheck kayak-audit-gauges \
         kayak-cert-expiry kayak-cert-renewal-test kayak-config-drift \
         kayak-editor-retention kayak-metadata-snapshot kayak-recap; do
  section "journal: $u"
  runS "journalctl --no-pager -n 100 -u $u.service"
done
section "OnFailure notifier instances (the @ template)"
runS "journalctl --no-pager -n 200 -u 'kayak-notify-failure@*.service'"


banner "7. fail2ban"
runS "fail2ban-client status"
runS "fail2ban-client status | awk -F'\t' '/Jail list:/ {print \$2}' | tr ',' '\\n' | sed 's/^ *//' | while read j; do
  [ -n \"\$j\" ] && echo \"--- jail: \$j ---\" && fail2ban-client status \"\$j\"
done"
section "recent ban activity (last 200 lines of fail2ban log)"
runS "tail -n 200 /var/log/fail2ban.log"


banner "8. TLS / certs"
runS "certbot certificates"
section "live cert chain — levels.mousebrains.com (HTTPS handshake from inside the box)"
run "openssl s_client -connect levels.mousebrains.com:443 -servername levels.mousebrains.com </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName"
section "live cert chain — levels-test.wkcc.org"
run "openssl s_client -connect levels-test.wkcc.org:443 -servername levels-test.wkcc.org </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName"
section "bridge cert state — /etc/nginx/certs/levels.wkcc.org.*"
runS "ls -la /etc/nginx/certs/ 2>/dev/null"
runS "test -f /etc/nginx/certs/levels.wkcc.org.cert && openssl x509 -in /etc/nginx/certs/levels.wkcc.org.cert -noout -subject -issuer -dates -ext subjectAltName"
runS "test -f /etc/nginx/certs/levels.wkcc.org.cert && diff <(openssl x509 -in /etc/nginx/certs/levels.wkcc.org.cert -modulus -noout) <(openssl rsa -in /etc/nginx/certs/levels.wkcc.org.privkey -modulus -noout) && echo 'OK: bridge cert/key moduli match' || echo 'MISMATCH'"
section "bridge cert SNI smoke (hit Hetzner by IP with wkcc SNI — pre-cutover, this proves the bridge would serve correctly the moment DNS flips)"
run "openssl s_client -connect 5.78.185.66:443 -servername levels.wkcc.org </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates -ext subjectAltName"
section "letsencrypt renewal config (authenticator must remain 'nginx' through Phase C)"
runS "ls -la /etc/letsencrypt/live/ /etc/letsencrypt/renewal/"
runS "grep -H ^authenticator /etc/letsencrypt/renewal/*.conf"


banner "9. Mail"
runS "mailq"
runS "tail -n 100 /var/log/mail.log"
run "test -f /home/pat/.msmtprc && cat /home/pat/.msmtprc | sed 's/password.*/password REDACTED/'"
section "msmtp smoke (TLS handshake only — no message sent)"
run "msmtp --serverinfo --host=smtp.gmail.com --port=587 --tls --tls-starttls 2>&1 | head -40"
section "ntfy reachability"
run "curl -sS -o /dev/null -w 'ntfy.sh HEAD %{http_code} in %{time_total}s\\n' -I https://ntfy.sh/ --max-time 5"


banner "10. File-perm spot checks"
runS "ls -la /etc/kayak/ 2>/dev/null"
runS "stat /etc/kayak/secrets.env /etc/kayak/runtime-config.json 2>/dev/null"
runS "ls -la /etc/nginx/conf.d/editor-env.conf"
runS "grep -E 'site_url' /etc/nginx/conf.d/editor-env.conf 2>/dev/null"
runS "ls -la /etc/nginx/conf.d/"
runS "ls -la /etc/nginx/snippets/"
runS "ls -la /etc/php/8.4/fpm/pool.d/"
runS "ls -la /etc/fail2ban/jail.local /etc/fail2ban/jail.d/ /etc/fail2ban/filter.d/kayak* /etc/fail2ban/filter.d/nginx-edit* /etc/fail2ban/filter.d/nginx-mal* /etc/fail2ban/filter.d/nginx-default-block.conf 2>/dev/null"
runS "ls -la /etc/ssh/sshd_config.d/"
runS "ls -la /etc/systemd/system/kayak-* /etc/systemd/system/multi-user.target.wants/kayak-* 2>/dev/null"
runS "ls -la /etc/sudoers.d/ 2>/dev/null"
runS "getfacl /home/pat /home/pat/kayak /home/pat/public_html /home/pat/DB 2>/dev/null | sed 's/^getfacl: //' | head -80"


banner "11. Drift detection: repo vs /etc (each pair: identical / unified diff)"
diff_pair() {
  local repo="$1"
  local live="$2"
  printf '\n--- %s  <->  %s ---\n' "$repo" "$live"
  if [ ! -f "$repo" ]; then echo "(repo missing)"; return; fi
  if ! sudo test -f "$live"; then echo "(live missing)"; return; fi
  sudo diff -u "$repo" "$live" 2>&1 | head -120 || true
  sudo diff -q "$repo" "$live" >/dev/null 2>&1 && echo "(identical)"
}
# nginx (current layout: conf/sites/ + conf/snippets/, NOT deploy/)
diff_pair "$REPO/conf/sites/levels-mousebrains-com"   /etc/nginx/sites-available/levels-mousebrains-com
diff_pair "$REPO/conf/sites/levels-test-wkcc-org"     /etc/nginx/sites-available/levels-test-wkcc-org
diff_pair "$REPO/conf/sites/levels-wkcc-org"          /etc/nginx/sites-available/levels-wkcc-org
diff_pair "$REPO/conf/snippets/levels-common.conf"    /etc/nginx/snippets/levels-common.conf
diff_pair "$REPO/conf/security-headers.conf"          /etc/nginx/snippets/security-headers.conf
diff_pair "$REPO/conf/security-headers-turnstile.conf" /etc/nginx/snippets/security-headers-turnstile.conf
diff_pair "$REPO/deploy/nginx-default-server"         /etc/nginx/sites-available/default
diff_pair "$REPO/deploy/nginx-editor-env.conf"        /etc/nginx/conf.d/editor-env.conf
diff_pair "$REPO/deploy/kayak-log-format.conf"        /etc/nginx/conf.d/kayak-log-format.conf
diff_pair "$REPO/deploy/ratelimit.conf"               /etc/nginx/conf.d/ratelimit.conf
# php-fpm
diff_pair "$REPO/deploy/kayak-fpm-pool.conf"          /etc/php/8.4/fpm/pool.d/kayak.conf
# logrotate
for f in $REPO/deploy/logrotate-*; do
  [ -e "$f" ] && diff_pair "$f" "/etc/logrotate.d/$(basename "$f" | sed 's/^logrotate-//')"
done
# fail2ban
diff_pair "$REPO/deploy/fail2ban/jail.local"                            /etc/fail2ban/jail.local
diff_pair "$REPO/deploy/fail2ban/jail.d/kayak-edit.conf"                /etc/fail2ban/jail.d/kayak-edit.conf
diff_pair "$REPO/deploy/fail2ban/jail.d/kayak-editor-auth.conf"         /etc/fail2ban/jail.d/kayak-editor-auth.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-edit-auth.conf"         /etc/fail2ban/filter.d/nginx-edit-auth.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-editor-auth.conf"       /etc/fail2ban/filter.d/nginx-editor-auth.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-malicious.conf"         /etc/fail2ban/filter.d/nginx-malicious.conf
diff_pair "$REPO/deploy/fail2ban/filter.d/nginx-default-block.conf"     /etc/fail2ban/filter.d/nginx-default-block.conf
# sshd
diff_pair "$REPO/deploy/sshd_config.d/hardening.conf"                   /etc/ssh/sshd_config.d/hardening.conf
# sysctl drop-ins (added during the 2026-05-10 preflight)
for f in $REPO/deploy/sysctl.d/*; do
  [ -e "$f" ] && diff_pair "$f" "/etc/sysctl.d/$(basename "$f")"
done
# nftables (if present in repo)
[ -f "$REPO/deploy/nftables.conf" ] && diff_pair "$REPO/deploy/nftables.conf" /etc/nftables.conf
# apt unattended-upgrades
[ -f "$REPO/deploy/apt.conf.d/50unattended-upgrades" ] && diff_pair "$REPO/deploy/apt.conf.d/50unattended-upgrades" /etc/apt/apt.conf.d/50unattended-upgrades
# sudoers drop-ins
diff_pair "$REPO/deploy/sudoers.d/kayak-emit-config"  /etc/sudoers.d/kayak-emit-config
diff_pair "$REPO/deploy/kayak-pipeline.sudoers"       /etc/sudoers.d/kayak-pipeline
# systemd units — all entries from UNITS[]. The .sh helper scripts are not
# installed to /etc/systemd/system/ (the .service files' ExecStart points
# directly at /home/pat/kayak/systemd/<script>.sh), so they're omitted.
for unit in "${UNITS[@]}"; do
  diff_pair "$REPO/systemd/$unit" "/etc/systemd/system/$unit"
done


banner "12. Pending OS updates"
run "apt list --upgradable 2>/dev/null | head -100"
runS "unattended-upgrade --dry-run -v 2>&1 | tail -40"
section "package counts + security holds"
run "dpkg -l | grep -c '^ii'"
runS "apt-mark showhold 2>/dev/null"
section "reboot-required?"
runS "ls -la /var/run/reboot-required /var/run/reboot-required.pkgs 2>/dev/null"
section "Debian release"
run "lsb_release -a 2>/dev/null"
run "cat /etc/os-release"


banner "13. Recent sshd auth activity"
runS "journalctl _COMM=sshd --no-pager -n 200"
runS "tail -n 100 /var/log/auth.log 2>/dev/null"
section "sshd_config posture"
runS "sshd -T 2>/dev/null | grep -E '^(passwordauthentication|permitrootlogin|pubkeyauthentication|kbdinteractiveauthentication|maxauthtries|loginGraceTime|x11forwarding|allowusers|allowgroups|usepam)'"
section "authorized keys (count + fingerprints, no key material)"
run "wc -l /home/pat/.ssh/authorized_keys 2>/dev/null"
run "ssh-keygen -lf /home/pat/.ssh/authorized_keys 2>/dev/null"


banner "14. CSP report tail"
run "ls -la /home/pat/logs/ 2>/dev/null"
run "test -f /home/pat/logs/csp.log && (echo 'size:'; wc -c /home/pat/logs/csp.log; echo 'last 100:'; tail -n 100 /home/pat/logs/csp.log) || echo 'no csp.log (no violations recorded)'"


banner "15. Disk + DB footprint"
run "du -sh /home/pat/DB /home/pat/public_html /home/pat/kayak/backups /home/pat/logs 2>/dev/null"
run "du -sh /home/pat/kayak /home/pat/.venv 2>/dev/null"
runS "du -sh /var/log /var/cache /var/lib /var/tmp /tmp 2>/dev/null"
runS "du -sh /var/log/* 2>/dev/null | sort -hr | head -20"
section "DB integrity + WAL state"
run "ls -lah /home/pat/DB/"
run "sqlite3 /home/pat/DB/kayak.db 'PRAGMA journal_mode; PRAGMA integrity_check; PRAGMA quick_check;' 2>&1"
run "sqlite3 /home/pat/DB/kayak.db 'PRAGMA wal_checkpoint(PASSIVE);' 2>&1"
section "schema_migrations highest version"
run "sqlite3 /home/pat/DB/kayak.db 'SELECT MAX(version), MAX(applied_at) FROM schema_migrations;' 2>&1"
section "any _new tables (partial migration debris)?"
run "sqlite3 /home/pat/DB/kayak.db '.schema' 2>&1 | grep -E '^CREATE TABLE [a-z_]+_new' || echo 'none'"
section "row counts (high-level)"
run "sqlite3 /home/pat/DB/kayak.db 'SELECT \"observation\", COUNT(*) FROM observation UNION ALL SELECT \"source\", COUNT(*) FROM source UNION ALL SELECT \"gauge\", COUNT(*) FROM gauge UNION ALL SELECT \"reach\", COUNT(*) FROM reach UNION ALL SELECT \"editor\", COUNT(*) FROM editor;'"
section "backup retention state (hourly + weekly)"
run "ls -lht /home/pat/kayak/backups/ 2>/dev/null | head -10"
run "ls /home/pat/kayak/backups/hourly-*.db.gz 2>/dev/null | wc -l"
run "ls /home/pat/kayak/backups/backup-*.db.gz  2>/dev/null | wc -l"
section "offsite (rclone) — last 30 lines of journal"
runS "journalctl -u kayak-backup-offsite --no-pager -n 40 | tail -30"
runS "rclone --config /home/pat/.config/rclone/rclone.conf lsf gdrive-crypt: 2>/dev/null | head -10 || echo 'rclone not configured for pat or remote missing'"


banner "16. crontab + at queue (should be empty — everything is systemd timers)"
run "crontab -l 2>/dev/null"
runS "ls /etc/cron.d/ /etc/cron.hourly/ /etc/cron.daily/ /etc/cron.weekly/ /etc/cron.monthly/ 2>/dev/null"
runS "atq 2>/dev/null"


banner "17. Time sync (chrony / systemd-timesyncd)"
runS "timedatectl"
runS "chronyc tracking 2>/dev/null || systemctl status systemd-timesyncd --no-pager"


banner "18. Logrotate state for our paths"
runS "ls -la /etc/logrotate.d/ | head -40"
runS "ls -la /var/log/nginx/ | head -40"
runS "ls -la /var/log/php* 2>/dev/null"


banner "19. Cutover-readiness probes (T0..T+3..T+30)"
section "Repo: cutover/wkcc-branding-flip branch state"
run "git -C $REPO log --oneline cutover/wkcc-branding-flip ^main 2>&1 | head"
run "git -C $REPO log --oneline main ^cutover/wkcc-branding-flip 2>&1 | head -5"
run "git -C $REPO diff --stat main..cutover/wkcc-branding-flip 2>&1 | tail -5"
section "Repo: current branding state in the 4 files the cutover commit touches"
run "grep -nH 'levels.\\(mousebrains\\|wkcc\\)' $REPO/LICENSE-DATA"
run "grep -n 'attribution' $REPO/src/kayak/web/build/_shared.py"
run "grep -n 'site_url' $REPO/deploy/nginx-editor-env.conf"
section "Repo: tests that pin the cutover-day attribution string"
run "grep -nH 'levels\\.\\(mousebrains\\|wkcc\\)' $REPO/tests/test_build_geojson_split.py"
section "Live attribution string in the most recent build output"
run "grep -o '\"attribution\":\"[^\"]*\"' /home/pat/public_html/static/reaches.geojson 2>/dev/null | head -1"
run "grep -o '\"attribution\":\"[^\"]*\"' /home/pat/public_html/static/sparklines.json 2>/dev/null | head -1"
section "Live site_url (from /etc/nginx/conf.d/editor-env.conf)"
runS "grep site_url /etc/nginx/conf.d/editor-env.conf"
section "Build mtime (drives the post-cutover GeoJSON refresh check)"
run "stat -c '%y  %n' /home/pat/public_html/index.html /home/pat/public_html/static/reaches.geojson /home/pat/public_html/static/sparklines.json 2>/dev/null"


banner "20. Apt sources + extra repos"
runS "cat /etc/apt/sources.list 2>/dev/null"
runS "ls /etc/apt/sources.list.d/ 2>/dev/null"
runS "cat /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null | head -100"


banner "21. Recent /var/log/nginx access volume by vhost (last hour)"
runS "for f in /var/log/nginx/levels-mousebrains.access.log /var/log/nginx/levels-test.access.log /var/log/nginx/levels-wkcc.access.log; do test -f \$f && echo \"\$f: \$(wc -l < \$f) lines\"; done"
section "Top 5 client IPs across all three vhosts, last full hour"
runS "awk '\$4 ~ /'\"\$(date -u -d '1 hour ago' +%H:)\"'/{print \$1}' /var/log/nginx/levels-*.access.log 2>/dev/null | sort | uniq -c | sort -rn | head -5 || true"


banner "done"
echo
echo "wrote: $OUT"
echo
echo "size:  $(wc -c < "$OUT") bytes"
echo
echo "next: paste the path in the chat (or upload the file) and I'll iterate."
