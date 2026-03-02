#!/usr/bin/env bash
# Install hardened configs for sshd, fail2ban, nginx, sysctl, nftables,
# unattended-upgrades, and DB file permissions.
# Usage: sudo ./hardening/install.sh
#
# After verifying everything works, delete the hardening/ directory.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root (use sudo)" >&2
    exit 1
fi

echo "=== sshd ==="
cp "$DIR/sshd-hardening.conf" /etc/ssh/sshd_config.d/hardening.conf
echo "  installed /etc/ssh/sshd_config.d/hardening.conf"
sshd -t && echo "  sshd config OK" || { echo "  ERROR: sshd config test failed!" >&2; exit 1; }

echo ""
echo "=== fail2ban ==="
cp "$DIR/jail.local" /etc/fail2ban/jail.local
echo "  installed /etc/fail2ban/jail.local"

echo ""
echo "=== nginx ==="
cp "$REPO/deploy/nginx-ratelimit.conf" /etc/nginx/conf.d/ratelimit.conf
echo "  installed /etc/nginx/conf.d/ratelimit.conf"
cp "$REPO/deploy/levels" /etc/nginx/sites-available/levels
echo "  installed /etc/nginx/sites-available/levels"
# Ensure symlink exists
ln -sf /etc/nginx/sites-available/levels /etc/nginx/sites-enabled/levels
nginx -t && echo "  nginx config OK" || { echo "  ERROR: nginx config test failed!" >&2; exit 1; }

echo ""
echo "=== sysctl hardening ==="
cp "$DIR/90-hardening.conf" /etc/sysctl.d/90-hardening.conf
echo "  installed /etc/sysctl.d/90-hardening.conf"
sysctl --system 2>&1 | tail -1
echo "  sysctl reloaded"

echo ""
echo "=== nftables firewall ==="
cp "$DIR/nftables.conf" /etc/nftables.conf
echo "  installed /etc/nftables.conf"
nft -c -f /etc/nftables.conf && echo "  nftables config OK" || { echo "  ERROR: nftables config test failed!" >&2; exit 1; }
systemctl enable nftables
systemctl restart nftables
echo "  nftables enabled and started"

echo ""
echo "=== unattended-upgrades ==="
cp "$DIR/50-unattended-upgrades-local" /etc/apt/apt.conf.d/50-unattended-upgrades-local
echo "  installed /etc/apt/apt.conf.d/50-unattended-upgrades-local"

echo ""
echo "=== msmtp (outgoing mail) ==="
apt-get install -y msmtp msmtp-mta mailutils
echo "  packages installed"
cp "$DIR/msmtprc" /etc/msmtprc
chown root:msmtp /etc/msmtprc
chmod 640 /etc/msmtprc
echo "  installed /etc/msmtprc (640 root:msmtp)"
cp "$DIR/msmtp-aliases" /etc/msmtp-aliases
chmod 644 /etc/msmtp-aliases
echo "  installed /etc/msmtp-aliases"
if grep -q 'APP_PASSWORD_HERE' /etc/msmtprc; then
    echo ""
    echo "  WARNING: /etc/msmtprc still contains APP_PASSWORD_HERE"
    echo "  Edit it now with: sudo nano /etc/msmtprc"
fi

echo ""
echo "=== DB file permissions ==="
chmod 660 /home/pat/DB/kayak.db
echo "  kayak.db set to 660"

echo ""
echo "=== Reloading services ==="
echo "  reloading sshd..."
systemctl reload sshd
echo "  restarting fail2ban..."
systemctl restart fail2ban
echo "  reloading nginx..."
systemctl reload nginx

echo ""
echo "=== Verification ==="
echo "sshd:"
sshd -T 2>/dev/null | grep -E '^(passwordauthentication|permitrootlogin|allowusers|x11forwarding|maxauthtries)' | sed 's/^/  /'
echo ""
echo "fail2ban jails:"
fail2ban-client status | sed 's/^/  /'
echo ""
echo "nginx: $(curl -sI https://localhost 2>/dev/null | grep -i strict-transport || echo 'check manually')"
echo ""
echo "sysctl:"
sysctl net.ipv4.conf.all.rp_filter net.ipv4.conf.all.accept_redirects net.ipv4.conf.all.send_redirects net.ipv4.conf.all.log_martians 2>/dev/null | sed 's/^/  /'
echo ""
echo "nftables:"
nft list ruleset | head -5 | sed 's/^/  /'
echo "  ... (run 'sudo nft list ruleset' for full output)"
echo ""
echo "unattended-upgrades auto-reboot:"
grep "Automatic-Reboot" /etc/apt/apt.conf.d/50-unattended-upgrades-local | sed 's/^/  /'
echo ""
echo "DB permissions:"
ls -l /home/pat/DB/kayak.db | sed 's/^/  /'
echo ""
echo "msmtp:"
ls -l /etc/msmtprc | sed 's/^/  /'
if grep -q 'APP_PASSWORD_HERE' /etc/msmtprc; then
    echo "  PASSWORD NOT SET — edit /etc/msmtprc before sending mail"
else
    echo "  Sending test email..."
    echo "Mail from $(hostname) is working" | mail -s "Test from $(hostname)" pat.kayak@gmail.com && echo "  test email sent" || echo "  test email failed (check: journalctl -t msmtp)"
fi

echo ""
echo "Done. IMPORTANT: Test SSH in a NEW terminal before closing this session!"
