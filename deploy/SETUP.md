# VPS Deployment Setup

Deploy kayak on a Hetzner CPX21 (3 vCPU AMD, 4 GB RAM, 80 GB disk) running
Debian 13 (Trixie) with nginx, PHP-FPM, and Let's Encrypt SSL.

Hostnames: `levels.mousebrains.com`, `levels.wkcc.org`

### Alternative: Oracle Cloud Free Tier

Oracle Cloud's Always Free tier provides an ARM VM (4 OCPU Ampere, 24 GB RAM,
200 GB disk, 10 TB/month egress) at no cost — not a trial, it runs indefinitely.
US West regions are San Jose (us-sanjose-1) and Phoenix (us-phoenix-1); San Jose
is closest to Oregon (~10-15ms latency). Debian 13 is not available as a platform
image but can be imported as a custom image (arm64 qcow2 from cloud.debian.org).

#### Oracle Cloud account setup

1. Go to https://cloud.oracle.com and click **Sign Up**.
2. A credit card is required for identity verification but will not be charged
   for Always Free resources.
3. For **Home Region**, choose **US West (San Jose)** for lowest latency to
   Oregon. Phoenix is the other US West option.

#### Import Debian 13 custom image

1. Download the Debian 13 arm64 cloud image:
   ```bash
   curl -LO https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-arm64.qcow2
   ```
2. Create an Object Storage bucket: hamburger menu > **Storage** > **Buckets** >
   **Create Bucket** > name it `images` > **Create**.
3. Upload the qcow2 file: click into the bucket > **Upload** > select the file.
4. Import as a custom image: hamburger menu > **Compute** > **Custom images** >
   **Import image**:
   - **Import from:** Object Storage bucket
   - **Bucket:** `images`
   - **Object name:** `debian-13-generic-arm64.qcow2`
   - **Image name:** `debian-13-arm64`
   - **Image type:** QCOW2
   - **Launch mode:** Paravirtualized
   - **Operating system:** Linux
   - **Compatible shapes:** Ampere
5. Wait for status to change from **Importing** to **Available** (~5-10 minutes).
   Refresh the Custom images page to check.

#### Networking (VCN)

Networking is created automatically when you create the instance (see below).
After the instance is running, open firewall ports for HTTP/HTTPS:

1. Go to the instance details page > click the **Subnet** link > click the
   **Default Security List**.
2. **Add Ingress Rules**:

   | Source CIDR | Protocol | Dest Port | Description |
   |-------------|----------|-----------|-------------|
   | `0.0.0.0/0` | TCP | 80 | HTTP |
   | `0.0.0.0/0` | TCP | 443 | HTTPS |

   SSH (port 22) is open by default.

#### SSH key

Generate a key for the instance (or use an existing one):

```bash
ssh-keygen -t ed25519 -C "oracle-kayak" -f ~/.ssh/id_oracle
```

#### Create the compute instance

1. Hamburger menu > **Compute** > **Instances** > **Create instance**.
2. **Name:** `kayak`
3. **Image and shape:**
   - Click **Change image** > **My images** > select `debian-13-arm64` >
     **Select image**.
   - Click **Change shape** > **Ampere** > **VM.Standard.A1.Flex**.
   - Set **OCPUs: 4**, **Memory: 24 GB** (full Always Free allowance).
   - Confirm it shows **Always Free eligible**.
4. **Networking:** Leave defaults — OCI creates a VCN with public subnet and
   internet gateway automatically. Ensure **Assign a public IPv4 address**
   is checked.
5. **SSH keys:** Paste the contents of `~/.ssh/id_oracle.pub`.
6. **Boot volume:** Click **Specify a custom boot volume size** > set to
   **200 GB** (Always Free maximum).
7. Click **Create**.

#### Capacity issues

ARM instances are frequently out of capacity in popular regions. If you get an
"Out of capacity" error:

- Retry manually every few minutes — capacity opens unpredictably.
- Try early morning US time when capacity is more available.
- Try a different region.
- Use the OCI CLI to retry in a loop:

  ```bash
  # Install OCI CLI: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm
  while true; do
      oci compute instance launch \
          --availability-domain "YOUR_AD" \
          --compartment-id "YOUR_COMPARTMENT_OCID" \
          --shape "VM.Standard.A1.Flex" \
          --shape-config '{"ocpus":4,"memoryInGBs":24}' \
          --image-id "YOUR_DEBIAN_IMAGE_OCID" \
          --subnet-id "YOUR_SUBNET_OCID" \
          --ssh-authorized-keys-file ~/.ssh/id_oracle.pub \
          --boot-volume-size-in-gbs 200 \
          --assign-public-ip true && break
      echo "Out of capacity, retrying in 60s..."
      sleep 60
  done
  ```

#### Connect and verify

Once the instance shows **Running**, find the public IP on the instance details
page:

```bash
ssh -i ~/.ssh/id_oracle debian@<PUBLIC_IP>
uname -m        # aarch64
cat /etc/os-release  # Debian 13 (Trixie)
free -h         # ~24 GB
nproc           # 4
df -h /         # ~200 GB
```

#### OS firewall (Oracle-specific)

The Debian cloud image may ship with iptables rules that block ports 80/443
even after opening them in the security list. Check and open if needed:

```bash
sudo iptables -L INPUT -n --line-numbers  # check current rules
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo apt install -y iptables-persistent
sudo netfilter-persistent save
```

#### Create the application user

The default user is `debian`. Create the `pat` user before proceeding:

```bash
sudo adduser pat
sudo -u pat mkdir -p /home/pat/.ssh
```

Then continue with **section 1** below — the setup is identical to Hetzner
since both run Debian 13.

#### Oracle Cloud gotchas

- **Do not terminate the instance** — if you delete an Always Free instance,
  you may not be able to provision a new one due to capacity.
- **Set a budget alert** — OCI console > **Billing** > **Budgets** > create a
  $1 alert to catch anything that leaves the free tier.
- **Idle reclamation** — Oracle emails after 60 days of inactivity on idle
  Always Free instances, threatening to reclaim them. The hourly pipeline timer
  counts as activity, so this is not an issue once deployed.

## 1. System packages

Debian 13 ships Python 3.13 and PHP 8.4.

```bash
sudo apt update
sudo apt install -y nginx php8.4-fpm php8.4-sqlite3 python3 python3-venv certbot
```

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
# Clone as the pat user
sudo -u pat git clone git@github.com:mousebrains/kayak_python.git /home/pat/kayak

cd /home/pat/kayak
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 3. Environment file

Create `/home/pat/kayak/.env`:

```bash
SQLITE_PATH=/home/pat/kayak/kayak.db
EDITOR_FEATURE=1
```

The pipeline, systemd services, and PHP all read from this file or its
variables. Maintainer access to `/edit.php` uses the `ed_sess` editor-session
cookie — sign in via `/login.php` with an email that has been promoted to
`status='maintainer'` (see `levels seed-maintainer`).

## 4. Initialize the database

```bash
cd /home/pat/kayak
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

`deploy/levels` is the canonical server-block config. Install it into
`sites-available/` and symlink from `sites-enabled/` so future updates only
need a copy + reload.

```bash
# Install the full config
sudo cp /home/pat/kayak/deploy/levels /etc/nginx/sites-available/levels
sudo ln -sf /etc/nginx/sites-available/levels /etc/nginx/sites-enabled/levels
sudo rm -f /etc/nginx/sites-enabled/kayak-acme.conf

# Test and reload
sudo nginx -t && sudo systemctl reload nginx
```

Subsequent updates (same pattern):

```bash
sudo cp /home/pat/kayak/deploy/levels /etc/nginx/sites-available/levels
sudo nginx -t && sudo systemctl reload nginx
```

The nginx config assumes:
- Document root: `/home/pat/public_html` (symlink → `/home/pat/kayak/public_html`)
- Database: `/home/pat/DB/kayak.db` (passed to PHP via `fastcgi_param SQLITE_PATH`)
- Cert path: `/etc/letsencrypt/live/levels.mousebrains.com/`
- Snippets: `/etc/nginx/snippets/security-headers.conf`, `security-headers-turnstile.conf`
- Rate-limit zones: `/etc/nginx/conf.d/ratelimit.conf` (see `deploy/ratelimit.conf`)

Edit `deploy/levels` to match actual paths if they differ.

## 7. PHP-FPM configuration

Edit the PHP-FPM pool to pass the database path. In `/etc/php/*/fpm/pool.d/www.conf`, add:

```ini
env[SQLITE_PATH] = /home/pat/kayak/kayak.db
```

Alternatively, the nginx config passes `SQLITE_PATH` via `fastcgi_param`, so this step is optional.

### Secrets (TURNSTILE_SECRET)

Keep the Cloudflare Turnstile site-verify secret out of nginx config files
(those are world-readable). Store it in a restricted env file and expose
it only to PHP-FPM workers.

All of the work below is packaged in `deploy/install-secrets.sh`:

```bash
# First run — installs /etc/kayak/secrets.env from the template and exits so
# you can edit it. The value is empty at this point; install-secrets.sh will
# refuse to proceed until TURNSTILE_SECRET is set.
sudo deploy/install-secrets.sh

# Fill in the real Turnstile secret (dash.cloudflare.com → Turnstile → site → Settings).
sudo -e /etc/kayak/secrets.env

# Second run — installs the PHP-FPM pool overlay + systemd drop-in,
# validates nginx, restarts php-fpm, reloads nginx.
sudo deploy/install-secrets.sh
```

PHP's `_turnstile_env()` helper prefers `getenv()` over `$_SERVER[]`, so once
PHP-FPM has the value in its environment the application code keeps working
without change.

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

## 8. Systemd timers

```bash
sudo /home/pat/kayak/systemd/install.service.sh
```

This copies the service/timer units to `/etc/systemd/system/`, enables and starts the timers. The `kayak-backup-offsite.service` is chained from `kayak-backup-weekly.service` via `OnSuccess=` and does not need its own timer.

Verify:

```bash
systemctl list-timers 'kayak-*' --all
```

Expected schedule (13 timers; most jittered via `RandomizedDelaySec=`):
- **kayak-pipeline.timer** — every hour at `:12` (fetches data, builds HTML)
- **kayak-backup-hourly.timer** — every hour at `:38` (sqlite `.backup` + WAL checkpoint; 24-copy retention; RPO ≤ 1h)
- **kayak-healthcheck.timer** — every hour at `:45` (data-freshness check)
- **kayak-decimate.timer** — daily at 02:32 (thins old observations, VACUUM)
- **kayak-editor-retention.timer** — daily at 03:45 (prunes expired editor sessions + magic links)
- **kayak-metadata-snapshot.timer** — daily at 04:30 (commits metadata-table drift to `data/db/*.csv`)
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

HSTS is already enabled at the server level in `deploy/levels` (the directive immediately follows the `include /etc/nginx/snippets/security-headers.conf;` line). After deploying a fresh `deploy/levels`, verify:

```bash
sudo nginx -t && sudo systemctl reload nginx
curl -sI https://levels.wkcc.org/ | grep -i strict-transport
# Expect: strict-transport-security: max-age=63072000; includeSubDomains
```

`preload` is intentionally OFF — submitting to hstspreload.org is a one-way commitment; revisit only after the operator has run with `preload` locally for a few months and is confident in maintaining HSTS forever.

If you also add `Strict-Transport-Security` to `/etc/nginx/snippets/security-headers.conf` for completeness, remove the per-server directive from `deploy/levels` to avoid duplicate headers in responses that fall through to server scope (browsers accept duplicates; just noisy).

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
| Document root | `/home/pat/public_html` → symlink to `kayak/public_html` |

### Setup steps

```bash
# 1. System packages (Debian 13)
sudo apt install -y nginx php8.4-fpm php8.4-sqlite3 python3 python3-venv sqlite3 acl

# 2. Virtual environment
python3 -m venv /home/pat/.venv
/home/pat/.venv/bin/pip install -e "/home/pat/kayak[dev]"

# 3. Directories
mkdir -p /home/pat/.config/kayak /home/pat/DB
ln -s /home/pat/kayak/public_html /home/pat/public_html

# 4. Environment file (~/.config/kayak/.env)
cat > /home/pat/.config/kayak/.env <<'EOF'
SQLITE_PATH=/home/pat/DB/kayak.db
DATABASE_URL=sqlite:////home/pat/DB/kayak.db
OUTPUT_DIR=/home/pat/public_html
EDITOR_FEATURE=1
EOF

# 5. ACLs for nginx (www-data)
setfacl -m u:www-data:x /home/pat                         # traverse only
setfacl -m u:www-data:x /home/pat/kayak                   # traverse only
setfacl -R -m u:www-data:rX /home/pat/kayak/public_html   # read static files
setfacl -R -d -m u:www-data:rX /home/pat/kayak/public_html  # default for new files
setfacl -R -m u:www-data:rX /home/pat/kayak/php            # read PHP files
setfacl -m u:www-data:rwx /home/pat/DB                     # DB read/write
setfacl -d -m u:www-data:rw /home/pat/DB                   # default for new DB files

# 6. Initialize and run
/home/pat/.venv/bin/levels init-db       # schema + seed states/sources/fetch_urls
/home/pat/.venv/bin/levels pipeline      # fetch live data, generate HTML
```

### config.py .env resolution

`kayak.config` checks `~/.config/kayak/.env` first, then falls back to the
default `load_dotenv()` search (current directory upward). PHP gets `SQLITE_PATH`
from nginx `fastcgi_param`, not from the `.env` file.

## Troubleshooting

**PHP returns 502 Bad Gateway:**
Check the socket path matches your PHP version:
```bash
ls /run/php/php*-fpm.sock
```
Update `fastcgi_pass` in `deploy/levels` to match.

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
