#!/bin/bash
# Security hardening script for levels-test.wkcc.org
# Review this script, then run: sudo bash scripts/security-harden.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Run with sudo: sudo bash $0"
    exit 1
fi

echo "=== Security Hardening Script ==="
echo ""

# --- 1. Generate new EDIT_PASSWORD and create restricted snippet ---
NEW_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)

cat > /etc/nginx/snippets/edit-password.conf <<EOF
fastcgi_param EDIT_PASSWORD ${NEW_PASSWORD};
EOF
chmod 600 /etc/nginx/snippets/edit-password.conf
chown root:root /etc/nginx/snippets/edit-password.conf

echo "[OK] Created /etc/nginx/snippets/edit-password.conf (mode 600)"

# --- 2. Add PHP rate limit zone (if not already present) ---
if ! grep -q 'zone=php' /etc/nginx/conf.d/ratelimit.conf 2>/dev/null; then
    echo 'limit_req_zone $binary_remote_addr zone=php:10m rate=2r/s;' >> /etc/nginx/conf.d/ratelimit.conf
    echo "[OK] Added PHP rate limit zone to ratelimit.conf"
else
    echo "[SKIP] PHP rate limit zone already exists in ratelimit.conf"
fi

# --- 3. Harden PHP session defaults in php.ini ---
PHP_INI="/etc/php/8.4/fpm/php.ini"
if [ -f "$PHP_INI" ]; then
    # session.cookie_httponly
    if grep -q '^session\.cookie_httponly' "$PHP_INI"; then
        sed -i 's/^session\.cookie_httponly.*/session.cookie_httponly = 1/' "$PHP_INI"
    elif grep -q '^;session\.cookie_httponly' "$PHP_INI"; then
        sed -i 's/^;session\.cookie_httponly.*/session.cookie_httponly = 1/' "$PHP_INI"
    else
        echo 'session.cookie_httponly = 1' >> "$PHP_INI"
    fi

    # session.cookie_secure
    if grep -q '^session\.cookie_secure' "$PHP_INI"; then
        sed -i 's/^session\.cookie_secure.*/session.cookie_secure = 1/' "$PHP_INI"
    elif grep -q '^;session\.cookie_secure' "$PHP_INI"; then
        sed -i 's/^;session\.cookie_secure.*/session.cookie_secure = 1/' "$PHP_INI"
    else
        echo 'session.cookie_secure = 1' >> "$PHP_INI"
    fi

    # session.cookie_samesite
    if grep -q '^session\.cookie_samesite' "$PHP_INI"; then
        sed -i 's/^session\.cookie_samesite.*/session.cookie_samesite = Strict/' "$PHP_INI"
    elif grep -q '^;session\.cookie_samesite' "$PHP_INI"; then
        sed -i 's/^;session\.cookie_samesite.*/session.cookie_samesite = Strict/' "$PHP_INI"
    else
        echo 'session.cookie_samesite = Strict' >> "$PHP_INI"
    fi

    # session.use_strict_mode
    if grep -q '^session\.use_strict_mode' "$PHP_INI"; then
        sed -i 's/^session\.use_strict_mode.*/session.use_strict_mode = 1/' "$PHP_INI"
    elif grep -q '^;session\.use_strict_mode' "$PHP_INI"; then
        sed -i 's/^;session\.use_strict_mode.*/session.use_strict_mode = 1/' "$PHP_INI"
    else
        echo 'session.use_strict_mode = 1' >> "$PHP_INI"
    fi

    echo "[OK] Hardened session settings in $PHP_INI"
else
    echo "[WARN] $PHP_INI not found — skipping session hardening"
fi

# --- 4. Create fail2ban filter for edit.php brute-force ---
cat > /etc/fail2ban/filter.d/nginx-edit-auth.conf <<'EOF'
# Matches 401 responses on /edit.php in the nginx access log
[Definition]
failregex = ^<HOST> .* "(?:GET|POST) /edit\.php\b[^"]*" 401
ignoreregex =
EOF

echo "[OK] Created fail2ban filter: nginx-edit-auth"

# --- 5. Create fail2ban jail for edit.php ---
cat > /etc/fail2ban/jail.d/kayak-edit.conf <<'EOF'
[nginx-edit-auth]
enabled  = true
filter   = nginx-edit-auth
logpath  = /var/log/nginx/kayak-access.log
maxretry = 5
findtime = 10m
bantime  = 1h
EOF

echo "[OK] Created fail2ban jail: nginx-edit-auth"

# --- 6. Install updated nginx config ---
cp /home/pat/kayak/conf/levels.nginx /etc/nginx/sites-enabled/levels
echo "[OK] Installed updated nginx config"

# --- 7. Test and reload ---
echo ""
echo "Testing nginx config..."
if nginx -t 2>&1; then
    echo ""
    systemctl reload nginx
    echo "[OK] nginx reloaded"
else
    echo "[ERROR] nginx config test failed — NOT reloaded"
    exit 1
fi

systemctl reload php8.4-fpm
echo "[OK] php8.4-fpm reloaded"

systemctl reload fail2ban
echo "[OK] fail2ban reloaded"

# --- 8. Summary ---
echo ""
echo "=== Done ==="
echo ""
echo "New edit.php credentials:"
echo "  User:     admin"
echo "  Password: ${NEW_PASSWORD}"
echo ""
echo "Save this password — it is stored only in /etc/nginx/snippets/edit-password.conf (mode 600)"
echo ""
echo "Verify with:"
echo "  curl -s -u admin:${NEW_PASSWORD} https://levels-test.wkcc.org/edit.php?id=526 | head -3"
