# VPS Deployment Setup

Deploy kayak on a Hetzner CPX21 (3 vCPU AMD, 4 GB RAM, 80 GB disk) running
Debian 13 (Trixie) with nginx, PHP-FPM, and Let's Encrypt SSL.

Hostnames: `levels.mousebrains.com`, `levels.wkcc.org`

### Alternative: Oracle Cloud Free Tier

Oracle Cloud's Always Free tier provides an ARM VM (4 OCPU Ampere, 24 GB RAM,
200 GB disk, 10 TB/month egress) at no cost — not a trial, it runs indefinitely.
The us-portland-1 (Portland, OR) region is available. Use Ubuntu 24.04 LTS
(ships Python 3.12, PHP 8.3) — the closest equivalent to Debian 13. Debian is
not available as an OCI platform image.

#### Oracle Cloud account setup

1. Go to https://cloud.oracle.com and click **Sign Up**.
2. A credit card is required for identity verification but will not be charged
   for Always Free resources.
3. For **Home Region**, choose a US West option (Phoenix or San Jose). You can
   create resources in any region regardless of home region.

#### Networking (VCN)

1. In the OCI console: hamburger menu > **Networking** > **Virtual cloud networks**.
2. **Start VCN Wizard** > **Create VCN with Internet Connectivity** > name it
   `kayak-vcn` > **Next** > **Create**.
3. Open firewall ports: click into the VCN > **Public Subnet** > **Default
   Security List** > **Add Ingress Rules**:

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
3. **Placement:** Select **us-portland-1** in the region selector if available.
4. **Image and shape:**
   - Click **Change image** > **Canonical Ubuntu** > **24.04 LTS aarch64** >
     **Select image**.
   - Click **Change shape** > **Ampere** > **VM.Standard.A1.Flex**.
   - Set **OCPUs: 4**, **Memory: 24 GB** (full Always Free allowance).
   - Confirm it shows **Always Free eligible**.
5. **Networking:** Select `kayak-vcn` and the public subnet. Ensure **Assign a
   public IPv4 address** is checked.
6. **SSH keys:** Paste the contents of `~/.ssh/id_oracle.pub`.
7. **Boot volume:** Click **Specify a custom boot volume size** > set to
   **200 GB** (Always Free maximum).
8. Click **Create**.

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
          --image-id "YOUR_UBUNTU_IMAGE_OCID" \
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
ssh -i ~/.ssh/id_oracle ubuntu@<PUBLIC_IP>
uname -m        # aarch64
lsb_release -a  # Ubuntu 24.04
free -h         # ~24 GB
nproc           # 4
df -h /         # ~200 GB
```

#### OS firewall (Oracle-specific)

Ubuntu on OCI ships with iptables rules that block ports 80/443 even after
opening them in the security list. Open them at the OS level:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

#### Create the application user

The default user is `ubuntu`. Create the `tpw` user before proceeding:

```bash
sudo adduser tpw
sudo -u tpw mkdir -p /home/tpw/.ssh
```

Then continue with **section 1** below. All `apt` commands work identically
on Ubuntu 24.04.

#### Oracle Cloud gotchas

- **Do not terminate the instance** — if you delete an Always Free instance,
  you may not be able to provision a new one due to capacity.
- **Set a budget alert** — OCI console > **Billing** > **Budgets** > create a
  $1 alert to catch anything that leaves the free tier.
- **Idle reclamation** — Oracle emails after 60 days of inactivity on idle
  Always Free instances, threatening to reclaim them. The hourly pipeline timer
  counts as activity, so this is not an issue once deployed.

## 1. System packages

Debian 13 ships Python 3.12 and PHP 8.3.

```bash
sudo apt update
sudo apt install -y nginx php8.3-fpm php8.3-sqlite3 python3 python3-venv certbot
```

Verify the PHP-FPM socket path:

```bash
ls /run/php/php8.3-fpm.sock
```

The nginx config expects `/run/php/php-fpm.sock`. Create a symlink so it works across PHP upgrades:

```bash
sudo ln -sf /run/php/php8.3-fpm.sock /run/php/php-fpm.sock
```

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
- **kayak-backup.timer** — weekly Sunday at 03:15 (SQLite backup with 4-copy retention)

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

## 11. Cloud backup (future — Hetzner Storage Box)

The local backup retains 4 weekly copies on the VPS. For off-site redundancy,
add a Hetzner Storage Box (BX11: 1 TB, ~3.80 EUR/month). Transfers stay
within the Hetzner network — no egress fees.

### Storage Box setup

1. Order a Storage Box from the Hetzner console. Note the credentials
   (e.g. `u123456`, hostname `u123456.your-storagebox.de`).

2. Enable SSH support in the Storage Box settings panel (disabled by default).

3. Set up SSH key auth from the VPS (the backup runs as user `tpw`):

   ```bash
   # Generate a key if tpw doesn't have one
   sudo -u tpw ssh-keygen -t ed25519 -C "kayak-backup" -f /home/tpw/.ssh/id_storagebox

   # Install the key on the Storage Box (port 23 for SSH)
   ssh-copy-id -p 23 -i /home/tpw/.ssh/id_storagebox.pub u123456@u123456.your-storagebox.de

   # Create the backup directory
   ssh -p 23 -i /home/tpw/.ssh/id_storagebox u123456@u123456.your-storagebox.de mkdir -p backups
   ```

4. Add the rsync step to `systemd/kayak-backup.sh`, after the local backup
   and before the retention cleanup:

   ```bash
   # Off-site copy to Hetzner Storage Box
   STORAGEBOX="u123456@u123456.your-storagebox.de"
   SSH_KEY="/home/tpw/.ssh/id_storagebox"
   rsync -az -e "ssh -p 23 -i $SSH_KEY" "$DEST" "$STORAGEBOX:backups/"
   ```

5. Test manually:

   ```bash
   sudo -u tpw /home/tpw/kayak/systemd/kayak-backup.sh
   ssh -p 23 -i /home/tpw/.ssh/id_storagebox u123456@u123456.your-storagebox.de ls -lh backups/
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
