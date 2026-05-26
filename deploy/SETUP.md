# VPS Deployment Setup

Deploy kayak on a Hetzner CPX11 (2 vCPU AMD, 2 GB RAM, 40 GB disk) running
Debian 13 (Trixie) with nginx, PHP-FPM, and Let's Encrypt SSL.

Hostnames: `levels.mousebrains.com`, `levels.wkcc.org`

Provision a CPX11 with the Debian 13 image in the Hetzner Cloud Console, add
your SSH key, and connect as `root@<IP>`. All steps below run on that box.

## 1. System packages

Debian 13 ships Python 3.13 and PHP 8.4.

```bash
sudo apt update
sudo apt install -y nginx libnginx-mod-http-headers-more-filter \
    php8.4-fpm php8.4-sqlite3 python3 python3-venv certbot acl
```

`libnginx-mod-http-headers-more-filter` supplies the `more_clear_headers`
directive used by `conf/snippets/levels-common.conf` (§6) — `nginx -t` fails
without it. `acl` is needed for the `www-data` ACL grants in §4.

Verify the PHP-FPM socket path:

```bash
ls /run/php/php8.4-fpm.sock
```

The nginx config expects `/run/php/php-fpm.sock`. Create a symlink so it works across PHP upgrades:

```bash
sudo ln -sf /run/php/php8.4-fpm.sock /run/php/php-fpm.sock
```

## 2. Application user and code

```bash
# Create the application user (the Hetzner cloud image logs in as root)
sudo adduser pat
sudo -u pat mkdir -p /home/pat/.ssh

# Add pat to the adm group so the log-reading timers/scripts work without
# root: kayak-recap reads journald (journalctl --unit kayak-*) and
# scripts/audit-t30.sh tails /var/log/{nginx/*,fail2ban,auth,mail}.log
# (mode 640, group adm).
sudo usermod -aG adm pat

# Clone as the pat user
sudo -u pat git clone git@github.com:mousebrains/kayak_python.git /home/pat/kayak

# The venv lives at ~/.venv, NOT inside the repo — every kayak-*.service unit's
# ExecStart= and the §7 sudoers grant invoke /home/pat/.venv/bin/levels.
cd /home/pat/kayak
python3 -m venv /home/pat/.venv
/home/pat/.venv/bin/pip install -e .
```

## 3. Environment file

Create the database directory and the config file at `~/.config/kayak/.env` —
the path `kayak.config` checks first and that every `kayak-*.service` unit
reads via `EnvironmentFile=` (§8). `levels` (Python) reads **`DATABASE_URL`**;
PHP resolves the DB path from `/etc/kayak/runtime-config.json` (written by
`levels emit-config`, §7), falling back to `SQLITE_PATH`. Both must point at the
**same** file, `/home/pat/DB/kayak.db` (outside the repo). The directory must
exist before `init-db` or SQLite fails with "unable to open database file":

```bash
mkdir -p /home/pat/DB /home/pat/.config/kayak /home/pat/public_html

cat > /home/pat/.config/kayak/.env <<'EOF'
DATABASE_URL=sqlite:////home/pat/DB/kayak.db
SQLITE_PATH=/home/pat/DB/kayak.db
OUTPUT_DIR=/home/pat/public_html
EDITOR_FEATURE=1
EOF
```

Maintainer access to `/edit.php` uses the `ed_sess` editor-session cookie —
sign in via `/login.php` with an email promoted to `status='maintainer'` (see
`levels seed-maintainer`).

## 4. Initialize the database

`init-db --no-seed` creates the schema and stamps migrations without the
`sources.yaml` seed; `import_metadata.py` then loads the gauges, reaches,
sources, and `gauge_source` links from the tracked `data/db/*.csv` snapshots.
Without those links the pipeline's `orphan-check` fails and the site renders
empty, so this order matters:

```bash
cd /home/pat/kayak
/home/pat/.venv/bin/levels init-db --no-seed
/home/pat/.venv/bin/python scripts/import_metadata.py
/home/pat/.venv/bin/levels pipeline    # first run — fetches data and generates HTML
```

Verify output was generated:

```bash
ls /home/pat/public_html/*.html
```

**Reach geometry (`reaches.json`).** `reach.geom` is excluded from
`reach.csv` (large, and not regenerable on prod — the DEM/NHD trace stack
is dev-only) and snapshotted to `data/db/reaches.json`. The
`import_metadata.py` call above applies it on a fresh install. It is
*not* migration-managed — the documented exception to "reach changes go
via a migration" — so a dev re-trace reaches prod like this:

1. dev: re-trace, then `python scripts/export_metadata.py` to refresh
   `data/db/reaches.json`; commit it.
2. prod: `scripts/deploy.sh` sees the changed `reaches.json` and runs
   `import_metadata.py --geom-only` automatically.

To apply geometry to the live DB by hand (without re-syncing CSV metadata):

```bash
/home/pat/.venv/bin/python scripts/import_metadata.py --geom-only
```

**Filesystem access for nginx/PHP (`www-data`).** The pipeline runs as `pat`,
but nginx and PHP-FPM run as `www-data`, which must traverse `pat`'s `0700`
home to reach the docroot and DB. The docroot (`/home/pat/public_html`, created
in §3) is a **real directory** that `levels build` (above) fills with a
self-contained site — HTML plus copied PHP/includes/static. The **only** repo
path `www-data` still reads is the operator status cache
(`/home/pat/kayak/var/status.html`, streamed by `/_internal/status`; R2.6 in the
round-3 plan would move it into the docroot and drop this last grant):

```bash
sudo -u pat mkdir -p /home/pat/kayak/var                      # status-cache dir (status.html lands here)
sudo setfacl -m  u:www-data:x     /home/pat /home/pat/kayak   # traverse
sudo setfacl -R -m u:www-data:rX  /home/pat/public_html       # read the built site
sudo setfacl -R -d -m u:www-data:rX /home/pat/public_html     # inherit for files build writes
sudo setfacl -R -m u:www-data:rX  /home/pat/kayak/var         # operator status cache
sudo setfacl -R -d -m u:www-data:rX /home/pat/kayak/var
sudo setfacl -R -m u:www-data:rwX /home/pat/DB                # WAL needs write even for read-only pages
sudo setfacl -R -d -m u:www-data:rwX /home/pat/DB
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

The live config is the three-vhost split under `conf/sites/` plus the shared
`conf/snippets/levels-common.conf` (PHP location, rate-limit bindings, logging,
`security.txt`). `scripts/check-config-drift.sh` holds the authoritative
repo→`/etc/` manifest; `deploy.sh` deliberately never touches `/etc/nginx/`, so
nginx config is installed and updated by hand.

```bash
cd /home/pat/kayak

# Snippets — included by the vhosts, so install these first
sudo cp conf/security-headers.conf           /etc/nginx/snippets/
sudo cp conf/security-headers-turnstile.conf /etc/nginx/snippets/
sudo cp conf/snippets/levels-common.conf     /etc/nginx/snippets/

# http-scope conf.d (rate-limit zones, log format, editor $site_url) — must
# load before the vhosts that reference them
sudo cp deploy/ratelimit.conf        /etc/nginx/conf.d/ratelimit.conf
sudo cp deploy/kayak-log-format.conf /etc/nginx/conf.d/kayak-log-format.conf
sudo cp deploy/nginx-editor-env.conf /etc/nginx/conf.d/editor-env.conf
sudo cp deploy/nginx-default-server  /etc/nginx/sites-available/default  # stock sites-enabled/default symlink picks this up

# Server blocks
sudo cp conf/sites/levels-mousebrains-com /etc/nginx/sites-available/
sudo cp conf/sites/levels-wkcc-org        /etc/nginx/sites-available/
sudo cp conf/sites/levels-test-wkcc-org   /etc/nginx/sites-available/   # soak host only

# Enable the production vhosts (on the soak/test box, enable levels-test-wkcc-org instead)
sudo ln -sf /etc/nginx/sites-available/levels-mousebrains-com /etc/nginx/sites-enabled/
sudo ln -sf /etc/nginx/sites-available/levels-wkcc-org        /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/levels  # drop the retired monolith if present

sudo nginx -t && sudo systemctl reload nginx
```

To update a config file later, `sudo cp` the changed repo file to its `/etc/`
path (run `scripts/check-config-drift.sh` to see exactly what differs), then
`sudo nginx -t && sudo systemctl reload nginx`.

The config assumes:
- Document root: `/home/pat/public_html` — a **real directory** written by `levels build` (`OUTPUT_DIR`), outside the repo
- Database: `/home/pat/DB/kayak.db` (PHP resolves the path from `/etc/kayak/runtime-config.json`, not nginx — see §7)
- Per-vhost certs under `/etc/letsencrypt/live/<hostname>/`

## 7. PHP-FPM configuration

PHP resolves the database path via the typed config
(`/etc/kayak/runtime-config.json`, written by `levels emit-config` — see the
typed-config subsection below), falling back to a `SQLITE_PATH` env var and
then a path relative to the script. On a standard install the JSON provides
it, so no pool edit is needed. To force a path via the pool instead, add to
`/etc/php/*/fpm/pool.d/www.conf`:

```ini
env[SQLITE_PATH] = /home/pat/DB/kayak.db
```

### Config files (`/etc/kayak/env` + `/etc/kayak/secrets.env`)

Two deploy-time files land under `/etc/kayak/`:

- `env` (mode 0644 root:root, world-readable) — `KAYAK_HOME=/home/pat`
  path indirection consumed by `kayak-*.service` `ExecStart=` lines
  and the helper shell scripts. NOT a secret.
- `secrets.env` (mode 0600 root:www-data) — the Cloudflare Turnstile
  site-verify secret, kept out of world-readable nginx config and
  exposed only to PHP-FPM workers.

All of the work below is packaged in `deploy/install-config.sh`:

```bash
# First run — installs /etc/kayak/env (if missing) + /etc/kayak/secrets.env
# from the templates and exits so you can fill in the Turnstile secret.
# install-config.sh refuses to proceed until TURNSTILE_SECRET is set.
sudo deploy/install-config.sh

# Fill in the real Turnstile secret (dash.cloudflare.com → Turnstile → site → Settings).
sudo -e /etc/kayak/secrets.env

# Second run — installs the PHP-FPM pool overlay + systemd drop-in,
# validates nginx, restarts php-fpm, reloads nginx.
sudo deploy/install-config.sh
```

`KAYAK_HOME` is parameterized via `Environment=KAYAK_HOME=/home/pat`
inside each `kayak-*.service` (the in-unit floor) plus
`EnvironmentFile=-/etc/kayak/env` (which overrides when present).
`ExecStart=` and `ExecStartPost=` then interpolate `${KAYAK_HOME}/…`.
Paths in `WorkingDirectory=` / `EnvironmentFile=` / `ReadWritePaths=`
remain literal — systemd doesn't expand env vars in those directives.

PHP reads `TURNSTILE_SECRET` from the JSON snapshot
`/etc/kayak/runtime-config.json` (Phase 2 of T3.3); the FPM-pool's
`env[TURNSTILE_SECRET] = $TURNSTILE_SECRET` re-export keeps a second
copy in `getenv()` as defense-in-depth.

`/edit.php` used to read `EDIT_PASSWORD` via HTTP Basic Auth; it now gates
maintainer access on the `ed_sess` editor-session cookie (same pattern as
/review.php). The `EDIT_USER` / `EDIT_PASSWORD` env vars and the
`edit-password.conf` snippet are no longer used.

If you're migrating an existing deployment that previously had
`HCAPTCHA_SECRET` (or any captcha secret) in nginx, **rotate the value**
before installing `/etc/kayak/secrets.env` — treat the old one as disclosed.

Captcha provider history: this site originally used hCaptcha (the secret
key was `HCAPTCHA_SECRET`). It switched to Cloudflare Turnstile on
2026-05-01 — Turnstile is invisible by default (no puzzle), single CSP
origin, and free with no usage caps.

### Typed config (`/etc/kayak/runtime-config.json`)

`scripts/deploy.sh` calls `sudo -n levels emit-config` between
`levels migrate` and `levels build` to refresh
`/etc/kayak/runtime-config.json` — the JSON snapshot PHP (and any
future consumer) reads instead of each component re-doing `getenv()`
calls. The grant lives in `deploy/sudoers.d/kayak-emit-config` and is
pinned to the exact `emit-config` invocation (it cannot run other
levels subcommands or modify anything outside `/etc/kayak/`).

Install once on a fresh host:

```bash
sudo install -m 440 -o root -g root \
    /home/pat/kayak/deploy/sudoers.d/kayak-emit-config \
    /etc/sudoers.d/kayak-emit-config
sudo visudo -cf /etc/sudoers.d/kayak-emit-config   # validate
```

Verify the grant works:

```bash
sudo -n /home/pat/.venv/bin/levels emit-config --dry-run | head -5
# Should print the first 5 lines of the JSON snapshot with no password prompt.
```

Inspect the resolved config any time (human-readable table):

```bash
/home/pat/.venv/bin/levels show-config
```

The JSON file is mode 0640 root:www-data — read it with
`sudo cat /etc/kayak/runtime-config.json` or via `levels show-config
--format json`. No `php-fpm reload` is needed for JSON content
changes; PHP re-reads the file once per request.

## 8. Systemd timers

```bash
sudo /home/pat/kayak/systemd/install.service.sh
```

This copies the service/timer units to `/etc/systemd/system/`, enables and starts the timers. The `kayak-backup-offsite.service` is chained from `kayak-backup-weekly.service` via `OnSuccess=` and does not need its own timer.

Verify:

```bash
systemctl list-timers 'kayak-*' --all
```

Expected schedule (15 timers; most jittered via `RandomizedDelaySec=`):
- **kayak-pipeline.timer** — every hour at `:12` (fetches data, builds HTML)
- **kayak-backup-hourly.timer** — every hour at `:38` (sqlite `.backup` + WAL checkpoint; 24-copy retention; RPO ≤ 1h)
- **kayak-healthcheck.timer** — every hour at `:45` (data-freshness check)
- **kayak-decimate.timer** — daily at 02:32 (thins old observations, VACUUM)
- **kayak-editor-retention.timer** — daily at 03:45 (prunes expired editor sessions + magic links)
- **kayak-metadata-snapshot.timer** — daily at 04:30 (commits metadata-table drift to `data/db/*.csv`)
- **kayak-status.timer** — daily at 03:30 (renders the `/_internal/status` operator dashboard to `var/status.html`)
- **kayak-fetch-osmb.timer** — daily at 03:30 (fetches Oregon State Marine Board hazard/access GeoJSON overlays)
- **kayak-cert-expiry.timer** — daily at 06:30 (Let's Encrypt cert health probe; pages on <21 days remaining)
- **kayak-cert-renewal-test.timer** — weekly Monday at 04:15 (`certbot renew --dry-run`)
- **kayak-recap.timer** — weekly Monday at 07:00 (pipeline-activity recap email; reads structured events from journald)
- **kayak-backup-weekly.timer** — weekly Sunday at 03:15 (SQLite snapshot + 4-copy retention; off-site upload chains via `OnSuccess=`)
- **kayak-audit-gauges.timer** — weekly Sunday at 03:29 (orphan-gauge + reach-mapping audit, emails maintainer digest)
- **kayak-config-drift.timer** — weekly Sunday at 05:30 (diff repo `conf/`/`deploy/`/`systemd/` against `/etc/`, alert on drift)
- **kayak-heartbeat.timer** — weekly Sunday at 06:00 (mail-path liveness)

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

## 10. HSTS

HSTS is set in the `security-headers.conf` / `security-headers-turnstile.conf` snippets (`add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;`), which every vhost includes — there is no separate per-server directive. After installing the snippets (§6), verify:

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -sI https://levels.wkcc.org/ | grep -i strict-transport
# Expect: strict-transport-security: max-age=63072000; includeSubDomains
```

`preload` is intentionally OFF — submitting to hstspreload.org is a one-way commitment; revisit only after the operator has run with `preload` locally for a few months and is confident in maintaining HSTS forever.

## 11. Fail2ban

`deploy/fail2ban/` mirrors the live `/etc/fail2ban/` layout. The defaults
ban after 5 failed logins in 10 minutes, escalate ban duration on repeat
offenders (up to a week), and run extra jails targeting credential-harvest
scanners and bare-IP probes against the default server block.

```bash
sudo apt-get install -y fail2ban
sudo cp deploy/fail2ban/jail.local                              /etc/fail2ban/jail.local
sudo cp deploy/fail2ban/jail.d/kayak-edit.conf                  /etc/fail2ban/jail.d/
sudo cp deploy/fail2ban/jail.d/kayak-editor-auth.conf           /etc/fail2ban/jail.d/
sudo cp deploy/fail2ban/filter.d/nginx-edit-auth.conf           /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/filter.d/nginx-editor-auth.conf         /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/filter.d/nginx-malicious.conf           /etc/fail2ban/filter.d/
sudo cp deploy/fail2ban/filter.d/nginx-default-block.conf       /etc/fail2ban/filter.d/
sudo systemctl enable --now fail2ban
sudo fail2ban-client status   # should list every jail above as active
```

Jails:

| Jail | Triggers on | Source log |
|---|---|---|
| `sshd` | failed SSH logins | journald |
| `nginx-http-auth` | nginx 401s (basic-auth) | `kayak-error.log` |
| `nginx-botsearch` | known bot paths | `kayak-access.log` |
| `nginx-limit-req` | rate-limit zone violations | `kayak-error.log` |
| `nginx-malicious` | `.env`, `.git`, `wp-config.php`, etc. | `access.log` + `kayak-access.log` |
| `nginx-default-block` | any hit on the bare-IP default vhost | `blocked-access.log` |
| `nginx-edit-auth` | 401 on `/edit.php` | `kayak-access.log` |
| `nginx-editor-auth` | 4xx / 429 on `/login.php` + `/auth.php`, plus POST `/login.php` (each submit costs one magic-link email) | `kayak-access.log` |

Inspect a jail's bans: `sudo fail2ban-client status nginx-editor-auth`.
Unban an IP: `sudo fail2ban-client set <jail> unbanip <addr>`.

## 12. SSH hardening

`deploy/sshd_config.d/hardening.conf` is the live `/etc/ssh/sshd_config.d/hardening.conf`:
key-only auth, AllowUsers pat, ed25519-only host + client keys, AEAD ciphers,
modern + post-quantum kex, IPv4-only (so fail2ban's IPv4 jails see everything).
`MaxStartups 5:50:30` allows up to five concurrent unauthenticated handshakes
before nginx-style 50% drop kicks in (hard cap at 30) — loose enough to allow
multi-session work and forwarded channels, tight enough to resist scanner
floods (with fail2ban already on `sshd`).

```bash
sudo cp deploy/sshd_config.d/hardening.conf /etc/ssh/sshd_config.d/hardening.conf
sudo sshd -t && sudo systemctl reload ssh
```

## 13. nftables firewall

`deploy/nftables.conf` is the live `/etc/nftables.conf`: drop-by-default
input policy, accept established/related, allow ICMP, rate-limit new SSH
to 3/min/IP (3:50:10 burst), accept HTTP/HTTPS, log+drop everything else.
Forward chain dropped (the box isn't a router). The fail2ban-managed
`f2b-table` adds dynamic ban sets that reject early in the input path.

```bash
sudo cp deploy/nftables.conf /etc/nftables.conf
sudo systemctl enable --now nftables
sudo nft list ruleset | head -40   # smoke check
```

## 14. sysctl kernel hardening

`deploy/sysctl.d/` carries four drop-ins for `/etc/sysctl.d/`:

| File | Purpose |
|---|---|
| `90-hardening.conf` | Reverse path filter, no ICMP redirects, syncookies on, martian logging |
| `90-swap.conf` | `vm.swappiness=10` (paired with the swap file in §15) |
| `92-local-hardening.conf` | `kernel.unprivileged_userns_clone=0`, eBPF disabled, kexec_load disabled |
| `99-hardening.conf` | `kernel.kptr_restrict=1` (block /proc kernel pointer leaks) |

```bash
sudo cp deploy/sysctl.d/*.conf /etc/sysctl.d/
sudo sysctl --system   # reload all and print effective values
```

## 15. Swap file

Hetzner CPX11 ships with 1.9 GB RAM and no swap. The pipeline peaks at
~600 MB and `levels decimate` VACUUM can hit ~1 GB; the right
absorption is a 4 GB swap file + `vm.swappiness=10` so the kernel
prefers evicting file cache over anonymous pages.

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# swappiness is set by deploy/sysctl.d/90-swap.conf in §14
free -h    # confirm: Swap 4.0Gi
```

## 16. Unattended security upgrades

Debian's `unattended-upgrades` package handles security updates. The
local override at `deploy/apt.conf.d/50unattended-upgrades-local` adds
auto-reboot at 04:00 if an update needs one (which is *before* the
03:15 weekly backup window starts; pick a different time if that ever
shifts).

```bash
sudo apt-get install -y unattended-upgrades apt-listchanges
sudo cp deploy/apt.conf.d/50unattended-upgrades-local \
    /etc/apt/apt.conf.d/50unattended-upgrades-local
sudo unattended-upgrade --dry-run -v | tail -10   # smoke check
```

## 17. Outgoing mail (msmtp + Gmail relay)

The box has no local MTA. Outbound alerts (heartbeat, audit digests,
contact form, magic-link emails) flow through msmtp to Gmail's SMTP.

1. Create a Google account app password at <https://myaccount.google.com/apppasswords>.
2. Install msmtp and aliases:

   ```bash
   sudo apt-get install -y msmtp msmtp-mta
   sudo cp deploy/msmtprc.example /etc/msmtprc
   sudo sed -i 's/APP_PASSWORD_HERE/your-16-char-app-password/' /etc/msmtprc
   sudo chown root:msmtp /etc/msmtprc
   sudo chmod 640 /etc/msmtprc
   sudo cp deploy/msmtp-aliases /etc/msmtp-aliases
   ```

3. Smoke-test:

   ```bash
   echo "test" | msmtp --debug pat.kayak@gmail.com 2>&1 | tail -20
   ```

## 18. Cloud backup (future — Hetzner Storage Box)

The local backup retains 4 weekly copies on the VPS. For off-site redundancy,
add a Hetzner Storage Box (BX11: 1 TB, ~3.80 EUR/month). Transfers stay
within the Hetzner network — no egress fees.

### Storage Box setup

1. Order a Storage Box from the Hetzner console. Note the credentials
   (e.g. `u123456`, hostname `u123456.your-storagebox.de`).

2. Enable SSH support in the Storage Box settings panel (disabled by default).

3. Set up SSH key auth from the VPS (the backup runs as user `pat`):

   ```bash
   # Generate a key if pat doesn't have one
   sudo -u pat ssh-keygen -t ed25519 -C "kayak-backup" -f /home/pat/.ssh/id_storagebox

   # Install the key on the Storage Box (port 23 for SSH)
   ssh-copy-id -p 23 -i /home/pat/.ssh/id_storagebox.pub u123456@u123456.your-storagebox.de

   # Create the backup directory
   ssh -p 23 -i /home/pat/.ssh/id_storagebox u123456@u123456.your-storagebox.de mkdir -p backups
   ```

4. Add the rsync step to `systemd/kayak-backup-weekly.sh`, after the local
   backup and before the retention cleanup:

   ```bash
   # Off-site copy to Hetzner Storage Box
   STORAGEBOX="u123456@u123456.your-storagebox.de"
   SSH_KEY="/home/pat/.ssh/id_storagebox"
   rsync -az -e "ssh -p 23 -i $SSH_KEY" "$DEST" "$STORAGEBOX:backups/"
   ```

5. Test manually:

   ```bash
   sudo -u pat /home/pat/kayak/systemd/kayak-backup-weekly.sh
   ssh -p 23 -i /home/pat/.ssh/id_storagebox u123456@u123456.your-storagebox.de ls -lh backups/
   ```

### Storage Box retention

The rsync copies each weekly backup to the Storage Box but does not
delete old remote copies. This is intentional — disk is cheap and the
DB is small. To apply the same 4-copy retention remotely, add after
the rsync:

```bash
# Clean remote backups: keep positions 0, 1, 3, 5 (same as local)
ssh -p 23 -i "$SSH_KEY" "$STORAGEBOX" bash -s <<'REMOTE'
cd backups
mapfile -t backups < <(ls -1r kayak-*.db 2>/dev/null)
keep=(0 1 3 5)
for i in "${!backups[@]}"; do
    skip=false
    for k in "${keep[@]}"; do [[ "$i" -eq "$k" ]] && skip=true && break; done
    [[ "$skip" == false ]] && rm -f "${backups[$i]}"
done
REMOTE
```

## Local Development Setup

For development on a non-production machine (e.g., Hetzner dev VPS). Uses separated
paths to keep the venv, database, and document root outside the git repo.

### Paths

| Component | Path |
|---|---|
| Git repo | `/home/pat/kayak` |
| Virtual environment | `/home/pat/.venv` |
| Configuration | `~/.config/kayak/.env` |
| SQLite database | `/home/pat/DB/kayak.db` |
| Document root | `/home/pat/public_html` (real dir, `OUTPUT_DIR` — outside the repo) |

### Setup steps

```bash
# 1. System packages (Debian 13)
sudo apt install -y nginx php8.4-fpm php8.4-sqlite3 python3 python3-venv sqlite3 acl

# 2. Virtual environment
python3 -m venv /home/pat/.venv
/home/pat/.venv/bin/pip install -e "/home/pat/kayak[dev]"

# 3. Directories (docroot is a real dir outside the repo — no symlink)
mkdir -p /home/pat/.config/kayak /home/pat/DB /home/pat/public_html

# 4. Environment file (~/.config/kayak/.env)
cat > /home/pat/.config/kayak/.env <<'EOF'
SQLITE_PATH=/home/pat/DB/kayak.db
DATABASE_URL=sqlite:////home/pat/DB/kayak.db
OUTPUT_DIR=/home/pat/public_html
EDITOR_FEATURE=1
EOF

# 5. ACLs for nginx (www-data) — docroot is a real dir; the only repo read is the status cache
mkdir -p /home/pat/kayak/var                              # status-cache dir (status.html)
setfacl -m u:www-data:x /home/pat /home/pat/kayak         # traverse
setfacl -R -m u:www-data:rX /home/pat/public_html         # read the built site
setfacl -R -d -m u:www-data:rX /home/pat/public_html      # default for new files
setfacl -R -m u:www-data:rX /home/pat/kayak/var           # operator status cache (status.html)
setfacl -R -m u:www-data:rwX /home/pat/DB                 # DB read/write (WAL needs write)
setfacl -R -d -m u:www-data:rwX /home/pat/DB              # default for new DB files

# 6. Initialize and run (same sequence as § 4 — plain init-db leaves every
#    source an orphan and renders an empty site)
/home/pat/.venv/bin/levels init-db --no-seed           # schema + stamped migrations
/home/pat/.venv/bin/python scripts/import_metadata.py  # gauges/reaches/sources/links from data/db/*.csv
/home/pat/.venv/bin/levels pipeline                    # fetch live data, generate HTML
```

### config.py .env resolution

`kayak.config` checks `~/.config/kayak/.env` first, then falls back to the
default `load_dotenv()` search (current directory upward). PHP gets the DB
path from `/etc/kayak/runtime-config.json` (written by `levels emit-config`),
not from the `.env` file.

## Troubleshooting

**PHP returns 502 Bad Gateway:**
Check the socket path matches your PHP version:
```bash
ls /run/php/php*-fpm.sock
```
Update `fastcgi_pass` in `conf/snippets/levels-common.conf` to match.

**Pipeline fails with permission errors:**
The systemd services run as user `pat`. Ensure the kayak directory and database are owned by that user:
```bash
sudo chown -R pat:pat /home/pat/kayak
```

**Certbot renewal fails:**
Ensure the ACME challenge location is still in the nginx config and `/var/www/certbot` exists:
```bash
sudo certbot renew --dry-run
```

**"No source_id set" warnings in pipeline logs:**
Normal on first run if Source records haven't been created yet. The pipeline auto-creates missing Source records for multi-station parsers (USBR). Run the pipeline a second time and these should resolve.
