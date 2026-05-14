# Off-host backup to Google Drive

Encrypted weekly backup of `kayak.db` to `pat.kayak@gmail.com`'s 15 GiB
Google Drive, layered on top of the local weekly snapshot in
`/home/pat/kayak/backups/`.

## Architecture

| When | Service | What |
|---|---|---|
| Hourly :38 | `kayak-backup-hourly.service` | sqlite `.backup` + WAL checkpoint + gzip; 24-copy retention. Local only. |
| Sun 03:15 | `kayak-backup-weekly.service` | sqlite `.backup` + gzip + local rotation (4 copies) |
| Sun 03:15+ | `kayak-backup-offsite.service` | rclone copy → `gdrive-crypt:`, prune to newest 26 |

Chained via `OnSuccess=kayak-backup-offsite.service` on the weekly-backup
unit — the offsite job only runs after a successful weekly local snapshot.
The hourly is local-only (RPO ≤ 1h); the weekly + offsite carry the
long-tail durability.
Failures in the offsite job route to `kayak-notify-failure@%n.service` and
trigger an email to `pat.kayak@gmail.com` via msmtp; they do **not** roll
back the local backup.

Local retention: 4 backups (positions 0, 1, 3, 5 → ~5 weeks).
Off-host retention: newest 26 (~6 months at one per week, ~2.5 GiB).

## Encryption

`rclone` uses a `crypt` overlay over the Drive remote. Plaintext filenames
and content are AES-256 encrypted client-side; what lands on Drive is an
opaque blob with an obfuscated filename. Two passphrases (`password`,
`password2`) live in `~/.config/rclone/rclone.conf` (chmod 600) and in
1Password under "Kayak backup encryption (rclone crypt)".

**If both copies are lost, off-host backups become unrecoverable.** The
local snapshots and Hetzner VPS snapshots are unaffected.

## Restore from off-host

Pick a backup, fetch it, verify, point Kayak at it.

### List available backups

```bash
rclone lsl gdrive-crypt:                 # plaintext names + size + mtime
rclone lsf gdrive-crypt: --files-only    # names only, sortable
```

### Download a specific backup

```bash
mkdir -p /tmp/restore
rclone copy gdrive-crypt:backup-YYYYMMDDTHHMMSSZ.db.gz /tmp/restore/
gunzip -t /tmp/restore/backup-*.db.gz   # integrity check, won't decompress
gunzip /tmp/restore/backup-*.db.gz
sqlite3 /tmp/restore/backup-*.db "PRAGMA integrity_check;"
```

The `integrity_check` should return `ok`. If it does not, pick an older
snapshot.

### Replace the live database

Stop services first, replace, restart. Kayak runs nginx + php-fpm + a few
systemd timers; stopping the timers prevents writes during swap.

```bash
sudo systemctl stop kayak-pipeline.timer kayak-decimate.timer kayak-audit-gauges.timer
sudo systemctl stop php8.2-fpm   # or whichever php-fpm version

# Move the live DB aside (don't delete — keeps a safety net)
mv /home/pat/DB/kayak.db /home/pat/DB/kayak.db.pre-restore

cp /tmp/restore/backup-*.db /home/pat/DB/kayak.db
chmod 664 /home/pat/DB/kayak.db

sudo systemctl start php8.2-fpm
sudo systemctl start kayak-pipeline.timer kayak-decimate.timer kayak-audit-gauges.timer
```

Then `levels pipeline` once to rebuild the static HTML against the
restored data.

## Recover from a lost rclone config

If `~/.config/rclone/rclone.conf` is gone — host rebuild, etc. — you need
both the OAuth token (one-time browser flow) and the crypt passphrases
(from 1Password).

```bash
# 1. Re-authorize Drive on a machine with a browser
rclone authorize "drive"
# Copy the JSON output

# 2. On the new host, write the gdrive section
mkdir -p ~/.config/rclone
cat > ~/.config/rclone/rclone.conf <<EOF
[gdrive]
type = drive
scope = drive
token = <paste JSON here>
EOF
chmod 600 ~/.config/rclone/rclone.conf

# 3. Add the crypt overlay using passphrases from 1Password
. /tmp/passphrases.env   # local file with password=… and password2=… lines
rclone config create gdrive-crypt crypt \
  remote=gdrive:kayak-backups \
  password="$password" \
  password2="$password2" \
  filename_encryption=standard \
  --obscure --non-interactive
unset password password2
shred -u /tmp/passphrases.env

# 4. Verify
rclone ls gdrive-crypt:
```

## Manual run

To upload an off-cycle snapshot (after a major DB change, before risky
ops, etc.):

```bash
sudo systemctl start kayak-backup-weekly.service   # runs both via OnSuccess chain
# OR, if the local backup is already current:
sudo systemctl start kayak-backup-offsite.service  # uploads newest local
```

## Health checks

```bash
# Newest off-host backup age
rclone lsl gdrive-crypt: | sort -k2,3 | tail -1

# Off-host count vs cap
rclone lsf gdrive-crypt: --files-only | wc -l

# Last service run + outcome
systemctl status kayak-backup-offsite.service
journalctl -u kayak-backup-offsite.service --since '7 days ago'
```
