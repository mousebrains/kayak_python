# VPS Deployment Setup

Deploy kayak on a Debian/Ubuntu VPS with nginx, PHP-FPM, and Let's Encrypt SSL.

Hostnames: `levels.mousebrains.com`, `levels.wkcc.org`

## 1. System packages

```bash
sudo apt update
sudo apt install -y nginx php-fpm php-sqlite3 python3 python3-venv certbot
```

Check which PHP-FPM version was installed and note the socket path:

```bash
php-fpm -v
ls /run/php/php*-fpm.sock
```

If the socket path differs from `/run/php/php-fpm.sock`, update the `fastcgi_pass` lines in `deploy/nginx.conf` (e.g. `/run/php/php8.2-fpm.sock`).

## 2. Application user and code

```bash
# Clone as the tpw user
sudo -u tpw git clone git@github.com:mousebrains/kayak_cpp.git /home/tpw/kayak

cd /home/tpw/kayak
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 3. Environment file

Create `/home/tpw/kayak/.env`:

```bash
SQLITE_PATH=/home/tpw/kayak/kayak.db
EDIT_USER=admin
EDIT_PASSWORD=changeme
```

The pipeline, systemd services, and PHP all read from this file or its variables.

## 4. Initialize the database

```bash
cd /home/tpw/kayak
.venv/bin/levels init-db
.venv/bin/levels pipeline    # first run — fetches data and generates HTML
```

Verify output was generated:

```bash
ls public_html/*.html
```

## 5. SSL certificates

Temporarily allow HTTP for the ACME challenge:

```bash
sudo mkdir -p /var/www/certbot
```

Create a minimal nginx config for the initial certificate request:

```bash
sudo tee /etc/nginx/sites-available/kayak-acme.conf <<'EOF'
server {
    listen 80;
    server_name levels.mousebrains.com levels.wkcc.org;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 444;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/kayak-acme.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Request the certificate:

```bash
sudo certbot certonly --webroot -w /var/www/certbot \
    -d levels.mousebrains.com -d levels.wkcc.org
```

Certbot auto-installs a systemd timer for renewal. Verify:

```bash
systemctl list-timers certbot.timer
```

## 6. Nginx configuration

```bash
# Install the full config
sudo cp /home/tpw/kayak/deploy/nginx.conf /etc/nginx/sites-available/kayak.conf
sudo ln -sf /etc/nginx/sites-available/kayak.conf /etc/nginx/sites-enabled/kayak.conf
sudo rm -f /etc/nginx/sites-enabled/kayak-acme.conf

# Test and reload
sudo nginx -t && sudo systemctl reload nginx
```

The nginx config assumes:
- Document root: `/home/tpw/kayak/public_html`
- Database: `/home/tpw/kayak/kayak.db`
- Cert path: `/etc/letsencrypt/live/levels.mousebrains.com/`

Edit `deploy/nginx.conf` to match actual paths if they differ.

## 7. PHP-FPM configuration

Edit the PHP-FPM pool to pass the database path. In `/etc/php/*/fpm/pool.d/www.conf`, add:

```ini
env[SQLITE_PATH] = /home/tpw/kayak/kayak.db
```

Alternatively, the nginx config passes `SQLITE_PATH` via `fastcgi_param`, so this step is optional.

Restart PHP-FPM:

```bash
sudo systemctl restart php*-fpm
```

## 8. Systemd timers (pipeline + decimate)

```bash
sudo /home/tpw/kayak/systemd/install.service.sh
```

This copies the service/timer units to `/etc/systemd/system/`, enables and starts the timers.

Verify:

```bash
systemctl list-timers kayak-*
```

Expected schedule:
- **kayak-pipeline.timer** — every hour at `:12` (fetches data, builds HTML)
- **kayak-decimate.timer** — daily at 02:32 (thins old observations)

## 9. Verify

```bash
# SSL working?
curl -I https://levels.mousebrains.com/

# Static pages served?
curl -s https://levels.mousebrains.com/Oregon.html | head -5

# PHP working?
curl -s "https://levels.mousebrains.com/api.php?id=1&type=flow&days=1" | head

# Gzip active?
curl -sI -H "Accept-Encoding: gzip" https://levels.mousebrains.com/Oregon.html | grep content-encoding
# Should show: content-encoding: gzip

# Pipeline running?
journalctl -u kayak-pipeline.service --since "1 hour ago" --no-pager
```

## 10. Enable HSTS

Once SSL is confirmed working, uncomment the HSTS header in `deploy/nginx.conf`:

```nginx
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
```

Then reload: `sudo nginx -t && sudo systemctl reload nginx`

## Troubleshooting

**PHP returns 502 Bad Gateway:**
Check the socket path matches your PHP version:
```bash
ls /run/php/php*-fpm.sock
```
Update `fastcgi_pass` in `deploy/nginx.conf` to match.

**Pipeline fails with permission errors:**
The systemd services run as user `tpw`. Ensure the kayak directory and database are owned by that user:
```bash
sudo chown -R tpw:tpw /home/tpw/kayak
```

**Certbot renewal fails:**
Ensure the ACME challenge location is still in the nginx config and `/var/www/certbot` exists:
```bash
sudo certbot renew --dry-run
```

**"No source_id set" warnings in pipeline logs:**
Normal on first run if Source records haven't been created yet. The pipeline auto-creates missing Source records for multi-station parsers (USBR). Run the pipeline a second time and these should resolve.
