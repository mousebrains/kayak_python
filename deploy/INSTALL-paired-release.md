# Paired-release install / cutover runbook (S7 / Batch 4C)

> **STATUS — VALIDATED end-to-end on a clean Debian 13 VM, 2026-06-14.** The whole
> sequence below — INCLUDING the rendered step 5 — ran from a virgin net-install to
> a serving paired-release host: homepage + DB-backed `description.php` both **200**,
> bare-IP **444**, and the activation gate passing on `render-units --list-units`
> (the 6 engine units verified from `/opt/kayak/current`), the nginx docroot root
> (ACME root surviving), and the FPM `open_basedir`. The serving + consumer config
> (**step 5**) is **rendered from `host.yaml`** by `levels render-units` /
> `render-serving` (PRs #193–#196), not hand-crafted. The legacy single-tree install
> (`/home/pat`, in-place `git pull`) stays in [`SETUP.md`](SETUP.md) until the WKCC
> host migrates (that live *migration* — vs. this virgin install — is increment 7).
>
> The validation used `/home/pat` + user `pat` to mirror WKCC; a clean non-WKCC
> install swaps the user/home/hostname/domains — those are `host.yaml` parameters
> (and `<svc>`/`<host>`/`<public-host>` below), not assumptions.

## Target layout (S7 + #3)

One deployment = one immutable engine commit + one immutable dataset commit +
this host's non-secret config, staged together and activated by one symlink switch.

| Path | Owner | Contents |
|---|---|---|
| `/opt/kayak/releases/<id>/` | root | immutable release: `venv/`, read-only `dataset/`, `runtime-config.json`, `release.json`, wheel + locks — **NO docroot** (it's a regenerable cache, below) |
| `/opt/kayak/current` | root | symlink → active release (atomic switch) |
| `/opt/kayak/.staging/` | root | deployer scratch — **real disk, never /tmp** (the wheel build + the ~650 MB DB backup overflow prod's ~1 GB `/tmp` tmpfs; default here, override with `KAYAK_DEPLOY_TMPDIR`) |
| `/var/cache/kayak/docroot` | `<svc>` | **served docroot** — a regenerable cache OUTSIDE the release (#3), rebuilt in place by the deploy AND the hourly pipeline; nginx roots here, FPM `open_basedir` leads with it. www-data-readable (ACL, step 6) |
| `/var/cache/kayak/{map-layers,gauge-metadata}` | `<svc>` | other generated caches the cutover relocates off the read-only release (fetch-osmb staging; the gauge-audit cache) |
| `<DB>` (`/home/<svc>/DB/kayak.db`) | `<svc>` | mutable SQLite DB (OUTSIDE the release) |
| `/etc/kayak/` | root | `env`, `secrets.env` (root-only), `runtime-config.json` (`0640 root:www-data`), `deploy.env`, `host.yaml` |
| `/usr/local/sbin/kayak-install-runtime-config` | root | the secret-merging config wrapper |

## Prerequisites

Debian 13. A **net-install** ships fewer packages than the Hetzner cloud image:

```bash
sudo apt update
sudo apt install -y nginx libnginx-mod-http-headers-more-filter \
    php8.4-fpm php8.4-sqlite3 python3 python3-venv certbot acl \
    git sqlite3 curl rsync rclone        # net-install omits sudo/rsync/git; add what's missing
```

`kayak-deploy` itself needs only `python3-venv` (stdlib venv/ensurepip — no system
pip); `git sqlite3 curl` are used by the orchestrator and the DB bootstrap. Repos
are **public**, so the host pulls over anonymous HTTPS — no deploy keys required
(use SSH deploy keys per SETUP.md §2.5 only if they go private again).

## Step order

> Each step is "run, observe, fix before continuing." A clean run means every
> verification passes.

### 1. Base prep — hostname, user, dirs

```bash
# Hostname (a net-install leaves it "debian"; sudo warns "unable to resolve host"
# until /etc/hosts matches)
sudo hostnamectl set-hostname <host>
sudo sed -i '/^127\.0\.1\.1/d' /etc/hosts
echo "127.0.1.1   <host>" | sudo tee -a /etc/hosts

# Service user in adm (log-reading timers) — '<svc>' = pat on WKCC
sudo usermod -aG adm <svc>          # (adduser <svc> first on a non-WKCC host)

# App dirs under the service user's home + the shared cache root (#3)
sudo -u <svc> mkdir -p /home/<svc>/DB /home/<svc>/var /home/<svc>/logs
sudo install -d -o <svc> -g <svc> /var/cache/kayak/docroot \
    /var/cache/kayak/map-layers /var/cache/kayak/gauge-metadata
```

### 2. Repos + host config

```bash
# Clone over HTTPS (public). The engine clone supplies the install tooling +
# orchestrator; check out the release branch you're deploying (main once 4C ships).
git clone https://github.com/mousebrains/kayak_python.git /home/<svc>/kayak
git -C /home/<svc>/kayak checkout main
git clone https://github.com/mousebrains/kayak_data.git /home/<svc>/kayak_data

# Per-user env (SITE_URL/SQLITE_PATH/DATASET_DIR/OUTPUT_DIR) — the deployer + the
# units source this. OUTPUT_DIR is the served docroot — the #3 shared cache.
install -d -m 0700 /home/<svc>/.config/kayak
cat > /home/<svc>/.config/kayak/.env <<EOF
SQLITE_PATH=/home/<svc>/DB/kayak.db
DATASET_DIR=/home/<svc>/kayak_data
OUTPUT_DIR=/var/cache/kayak/docroot
SITE_URL=https://<public-host>
EOF
chmod 0600 /home/<svc>/.config/kayak/.env

# /etc/kayak/env + secrets.env templates, then fill the Turnstile secret
cd /home/<svc>/kayak
sudo deploy/install-config.sh            # first run: writes templates, exits
sudo -e /etc/kayak/secrets.env           # set TURNSTILE_SITE_KEY / TURNSTILE_SECRET
                                         # (Cloudflare always-pass TEST keys for a test box:
                                         #  1x00000000000000000000AA / 1x0000000000000000000000000000000AA)

# host.yaml — the non-secret host shape the renderers read (step 5). Defaults are
# the current WKCC values; the cutover FLIPS the generated-cache paths off the
# now-read-only release. A non-WKCC host also sets service_user/service_home/
# cert_host/server_names here.
cat <<EOF | sudo tee /etc/kayak/host.yaml
docroot: /var/cache/kayak/docroot
map_layers_dir: /var/cache/kayak/map-layers
gauge_metadata_cache: /var/cache/kayak/gauge-metadata/gauges.db
EOF

# The root config wrapper (installs runtime-config.json with the secret merge)
sudo install -m 0755 -o root -g root deploy/kayak-install-runtime-config.sh \
    /usr/local/sbin/kayak-install-runtime-config
sudo install -m 440 -o root -g root deploy/sudoers.d/kayak-emit-config \
    /etc/sudoers.d/kayak-emit-config
sudo visudo -cf /etc/sudoers.d/kayak-emit-config

# deploy.env — repos + app user. Leave SERVING_CUTOVER unset until step 5.
sudo install -m 0644 deploy/deploy.env.example /etc/kayak/deploy.env
sudo sed -i \
  -e 's#^ENGINE_REPO=.*#ENGINE_REPO=https://github.com/mousebrains/kayak_python.git#' \
  -e 's#^DATASET_REPO=.*#DATASET_REPO=https://github.com/mousebrains/kayak_data.git#' \
  /etc/kayak/deploy.env
sudo sed -i 's/^#\?ENGINE_BRANCH=.*/ENGINE_BRANCH=main/' /etc/kayak/deploy.env
# KAYAK_APP_USER is in the example (verify it = <svc>). Any value with spaces (e.g.
# KAYAK_UNITS) MUST be quoted — deploy.env is sourced by the shell. (sed UNCOMMENTS
# the example line rather than appending, so there's no commented+active duplicate.)
```

### 3. Stage-only dry run

```bash
sudo env KAYAK_DEPLOY_CONF=/etc/kayak/deploy.env \
  /home/<svc>/kayak/deploy/kayak-deploy.sh \
  --engine-ref <40hex> --dataset-ref <40hex> --stage-only
```

Inspect `/opt/kayak/releases/<id>/`: `venv/`, `dataset/`, `release.json` digests,
normalized `runtime-config.json` (no secrets, no scratch paths), and **no
`docroot/`** (it's the shared cache now). No system mutation; scratch lands in
`/opt/kayak/.staging` (real disk) — `df /tmp` should not move.

### 4. Bootstrap the DB — first install only (REQUIRED)

> **Validated finding:** the first activation runs `migrate`, **not** `init-db`.
> On a virgin host `levels migrate` dies with `no such table: reach`. So the DB is
> initialized once, here, using the **staged release's venv** (no host venv needed):

```bash
R=/opt/kayak/releases/<id>            # the dir step 3 printed
DBURL=sqlite:////home/<svc>/DB/kayak.db
sudo -u <svc> env DATABASE_URL=$DBURL                       $R/venv/bin/levels init-db
sudo -u <svc> env DATABASE_URL=$DBURL DATASET_DIR=$R/dataset $R/venv/bin/levels sync-metadata
sudo -u <svc> env DATABASE_URL=$DBURL DATASET_DIR=$R/dataset $R/venv/bin/levels import-metadata
sqlite3 /home/<svc>/DB/kayak.db 'select count(*) from reach;'   # verify populated
```

> A fresh `init-db` stamps the engine's current migration files (fewer rows than a
> long-lived live DB's `schema_migrations`); the activation's `migrate` then reports
> "No pending migrations." Expected.

### 5. Serving + consumer config — the cutover (rendered from `host.yaml`)

> **Rendered, not hand-crafted.** `render-units` emits the systemd drop-ins and
> `render-serving` emits the nginx `root` + FPM `open_basedir` from `host.yaml`, so
> the values are deterministic. Run them from the **staged release's venv** (`$R`
> from step 4). The deployer's activation gate (step 7) re-verifies all of this, so
> a mistake here is caught before it serves.

> The renderers read `host.yaml` from its default `/etc/kayak/host.yaml` (installed
> in step 2), so no env is needed — `sudo` would drop a `KAYAK_HOST_CONFIG` export
> anyway.

```bash
cd /home/<svc>/kayak

# 5a. nginx: copy the static config (snippets / conf.d / vhosts), then set the
#     docroot `root` from the renderer (not hand-typed). The vhosts' server_name /
#     cert / log lines are static + correct for this host.
sudo cp conf/security-headers.conf conf/security-headers-turnstile.conf \
        conf/snippets/levels-common.conf /etc/nginx/snippets/
sudo cp deploy/ratelimit.conf deploy/kayak-log-format.conf deploy/nginx-editor-env.conf \
        conf/mime-extras.conf /etc/nginx/conf.d/
sudo cp deploy/nginx-default-server /etc/nginx/sites-available/default
sudo cp conf/sites/levels-*.conf /etc/nginx/sites-available/ 2>/dev/null || \
     sudo cp conf/sites/levels-mousebrains-com conf/sites/levels-wkcc-org /etc/nginx/sites-available/
sudo install -d /run/kayak-serving
sudo $R/venv/bin/levels render-serving --out-dir /run/kayak-serving   # nginx-levels-docroot.conf + fpm-open-basedir.conf
# Replace the docroot `root` line ONLY (NOT the ACME `root /var/www/certbot;`):
sudo sed -i "s#^[[:space:]]*root .*/public_html;#$(cat /run/kayak-serving/nginx-levels-docroot.conf)#" \
     /etc/nginx/snippets/levels-common.conf
sudo ln -sf /etc/nginx/sites-available/levels-wkcc-org /etc/nginx/sites-enabled/
# TLS. Public host: `certbot certonly` (SETUP.md §5). Private test VM: self-signed
# at the LE paths the vhosts reference + certbot's two include files (else `nginx -t`
# fails on the missing certs/includes):
sudo mkdir -p /etc/letsencrypt/live/levels.mousebrains.com
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout /etc/letsencrypt/live/levels.mousebrains.com/privkey.pem \
  -out    /etc/letsencrypt/live/levels.mousebrains.com/fullchain.pem \
  -subj "/CN=levels.mousebrains.com" \
  -addext "subjectAltName=DNS:levels.mousebrains.com,DNS:<public-host>"
printf '%s\n' 'ssl_session_cache shared:le_nginx_SSL:10m;' 'ssl_session_timeout 1440m;' \
  'ssl_session_tickets off;' 'ssl_protocols TLSv1.2 TLSv1.3;' 'ssl_prefer_server_ciphers off;' \
  | sudo tee /etc/letsencrypt/options-ssl-nginx.conf
sudo openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048
# The default-server block (deploy/nginx-default-server — 444 on bare-IP/SNI-less
# requests) needs a self-signed dummy cert, or `nginx -t` fails on the missing
# file. (Required on EVERY install — also a gap in SETUP.md's legacy path.)
sudo install -d /etc/nginx/ssl
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout /etc/nginx/ssl/dummy.key -out /etc/nginx/ssl/dummy.crt -subj "/CN=invalid"
sudo nginx -t

# 5b. FPM pool, then set open_basedir from the renderer (leads with the docroot;
#     #3 dropped the /opt/kayak/releases entry — PHP execs the self-contained
#     docroot, never the release).
sudo deploy/install-config.sh            # second run: installs FPM pool, nginx -t, reloads
sudo sed -i "s#^[[:space:]]*php_admin_value\[open_basedir\].*#$(cat /run/kayak-serving/fpm-open-basedir.conf)#" \
     /etc/php/8.4/fpm/pool.d/kayak.conf
sudo systemctl restart php8.4-fpm

# 5c. systemd units, then the cutover drop-ins from render-units — one per ENGINE
#     consumer (the 6 that run `levels` from the venv, audit-gauges included since
#     #191). Each drop-in re-points ExecStart at /opt/kayak/current/venv + fixes
#     DATASET_DIR/OUTPUT_DIR/ReadWritePaths. The checkout-script consumers
#     (healthcheck/config-drift/recap/heartbeat) keep running from the repo — they
#     are NOT engine units and the gate treats them as host-level automatically.
sudo /home/<svc>/kayak/systemd/install.service.sh
sudo $R/venv/bin/levels render-units --out-dir /etc/systemd/system
# (render-units WARNs that /opt/kayak/current doesn't exist yet, and
# `systemd-analyze verify` reports the ExecStart binary "not executable" for the
# same reason — both EXPECTED pre-cutover: the drop-ins reference `current`, the
# timers are stopped just below, and step 7 creates `current` then starts the
# consumers. The merged unit STRUCTURE is valid; re-verify after step 7.)
sudo systemctl daemon-reload && sudo systemctl stop 'kayak-*.timer'

# 5d. ONLY after nginx + FPM + units all point at the docroot/current: flip the
#     cutover flag + the serving-path knobs the gate verifies (fail-closed when
#     SERVING_CUTOVER=yes, so both are required).
sudo sed -i \
  -e 's/^#\?SERVING_CUTOVER=.*/SERVING_CUTOVER=yes/' \
  -e 's,^#\?KAYAK_NGINX_DOCROOT_CONF=.*,KAYAK_NGINX_DOCROOT_CONF=/etc/nginx/snippets/levels-common.conf,' \
  -e 's,^#\?KAYAK_FPM_POOL=.*,KAYAK_FPM_POOL=/etc/php/8.4/fpm/pool.d/kayak.conf,' \
  /etc/kayak/deploy.env   # uncomment the example lines (no commented+active duplicate)
```

### 6. ACLs (REQUIRED — else DB-backed PHP 503 / docroot 403)

> **Validated finding:** without the home ACLs, `www-data` can't traverse to the
> DB and DB-backed PHP returns **503** while the static homepage still serves 200
> (a misleading symptom). The shared docroot needs its own recursive **and
> default** ACL — `build` mints fresh files every run, so without the `-d` default
> each newly built page **403**s.

```bash
# Home: traverse + DB/var/logs (recursive + default)
sudo setfacl -m  u:www-data:x     /home/<svc>
sudo setfacl -R -m u:www-data:rwX /home/<svc>/DB;   sudo setfacl -R -d -m u:www-data:rwX /home/<svc>/DB
sudo setfacl -R -m u:www-data:rX  /home/<svc>/var;  sudo setfacl -R -d -m u:www-data:rX  /home/<svc>/var
sudo setfacl    -m u:www-data:rwx /home/<svc>/logs; sudo setfacl    -d -m u:www-data:rwx /home/<svc>/logs
# Shared docroot (#3): recursive + DEFAULT read for the web user
sudo setfacl -R -m  u:www-data:rX  /var/cache/kayak/docroot
sudo setfacl -R -d -m u:www-data:rX /var/cache/kayak/docroot
sudo -u www-data test -r /home/<svc>/DB/kayak.db && echo "www-data can read the DB ✓"
```

### 7. First activation + verify

```bash
sudo env KAYAK_DEPLOY_CONF=/etc/kayak/deploy.env \
  /home/<svc>/kayak/deploy/kayak-deploy.sh --engine-ref <40hex> --dataset-ref <40hex>
```

The gate refuses unless the host is fully cut over: it asks the release engine for
the units to verify (`render-units --list-units`), checks each runs from
`/opt/kayak/current` + pins `OUTPUT_DIR=/var/cache/kayak/docroot`, and checks nginx
roots there (with the ACME `root` surviving) + FPM `open_basedir` leads with it.
Verify:
- `readlink /opt/kayak/current` → the new release; `/opt/kayak/maintenance` cleared.
- homepage **and a DB-backed page** serve 200 (`description.php?h=1` → a reach page,
  not 503/403). Re-check after the ACL step if it errors.
- `systemctl show -p ExecStart --value kayak-pipeline.service` → `/opt/kayak/current/venv/bin/levels`.

> **Validated:** a *failed* first activation (bad `HEALTH_URL`) on a virgin host —
> no previous release — correctly **removes `current`, leaves the host in
> maintenance with consumers stopped, and restores the DB backup**.

### 8. Host hardening

TLS (real certbot), fail2ban, firewall, mail, swap, unattended-upgrades — same as
SETUP.md §5/§10–§17; point status/backup/cert checks at this host's values.

## Migrating a RUNNING host (the live WKCC cutover) — validated 2026-06-14

The steps above install a *virgin* host. The live host is a **running** system on
`scripts/deploy.sh` + `/home/<svc>/public_html` + the editable `.venv` install +
the live DB. Cutting it over **without dropping serving** rehearsed cleanly on a
live-equivalent VM clone (full live DB — 4.46 M obs, 77 migrations, served the
whole time). The trick: build the paired-release ALONGSIDE the live one and
**defer the nginx reload / FPM restart until AFTER the activation builds the new
docroot**, then flip with a graceful reload. Differs from the virgin path: NO
`init-db` (reuse the live DB — `migrate` reports "No pending" when the host already
runs the deployed engine), NO base prep / repo clone (present), and the serving
config is edited in place but not reloaded until the end.

### Where the old single-tree layout maps to

The pre-cutover host kept everything under `/home/<svc>`. The cutover moves only
what must become immutable (the engine + dataset) or regenerable (the docroot);
the mutable state and the un-re-pointed shell consumers stay put. The table is the
authoritative "what moved / what didn't" for the live WKCC dirs:

| Old (`/home/<svc>/…`) | New location | Disposition |
|---|---|---|
| `~/kayak` | engine → `/opt/kayak/current/venv`; **checkout stays** | The running *engine* moves into the immutable release. The `~/kayak` checkout **stays**: `kayak-deploy.sh` + the committed `conf/`/`deploy/` config run from it, and the shell-script consumers the cutover does **not** re-point live here — `kayak-recap`, `kayak-heartbeat`, `kayak-config-drift`, `kayak-healthcheck`, `kayak-cert-expiry`, `kayak-backup-*` all `ExecStart` from `~/kayak/{scripts,systemd}/`. Keep it on `main`. (Its two in-tree caches `var/osmb` + `Gauge-metadata-cache/` relocate to `/var/cache/kayak/{map-layers,gauge-metadata}` — see `host.yaml`.) |
| `~/.venv` | engine → `/opt/kayak/current/venv`; **stays** | The cutover re-points the 3 engine units (`kayak-status`, `kayak-fetch-osmb`, `kayak-audit-gauges`) at the release venv. `~/.venv` **stays** only because `kayak-recap.sh` still calls `~/.venv/bin/python3 ~/kayak/scripts/recap.py`; retire when the last shell consumer moves. |
| `~/kayak_data` | release snapshot → `/opt/kayak/current/dataset` (read-only); **clone stays** | Each release snapshots the dataset at its pinned commit; consumers read `DATASET_DIR=/opt/kayak/current/dataset`. The `~/kayak_data` clone **stays** as the working checkout the deployer snapshots from. |
| `~/public_html` | `/var/cache/kayak/docroot` (#3); **old one retired** | Moved OUT of the home to a regenerable shared cache: nginx roots here, FPM `open_basedir` leads with it, rebuilt by both the deploy and the hourly pipeline. Old `~/public_html` becomes unused — leave as a fallback, clean up later. |
| `~/DB/kayak.db` | **unchanged** | Mutable SQLite DB, OUTSIDE every release by design — survives cutover and rollback (the deployer backs it up pre-migrate). |
| `~/var/status.html` | **unchanged** | Operator status page (`HostConfig.status_output`); `kayak-status` writes it from the release venv but the path is unchanged; in FPM `open_basedir`. |
| `~/logs` | **unchanged** | CSP-report + app logs; in FPM `open_basedir`. |
| `~/.config/kayak/.env` | **unchanged** (one edit) | App-user env stays; the cutover only repoints `OUTPUT_DIR` → `/var/cache/kayak/docroot`. The new root-owned *host*/*deploy* config lives at `/etc/kayak/{host.yaml,deploy.env}`. |
| `~/backups` | **unchanged** | Host backup target (`HostConfig.backup_dir`); `kayak-backup-*` units write here from `~/kayak/systemd/`. |

```bash
cd /home/<svc>/kayak

# Phase 0 — pre-stage ALONGSIDE the running site (no disruption: still serving public_html)
sudo install -d -o <svc> -g <svc> /var/cache/kayak/docroot /var/cache/kayak/map-layers /var/cache/kayak/gauge-metadata
printf 'docroot: /var/cache/kayak/docroot\nmap_layers_dir: /var/cache/kayak/map-layers\ngauge_metadata_cache: /var/cache/kayak/gauge-metadata/gauges.db\n' | sudo tee /etc/kayak/host.yaml >/dev/null
sudo install -m 0644 deploy/deploy.env.example /etc/kayak/deploy.env
sudo sed -i -e 's#^ENGINE_REPO=.*#ENGINE_REPO=https://github.com/mousebrains/kayak_python.git#' \
            -e 's#^DATASET_REPO=.*#DATASET_REPO=https://github.com/mousebrains/kayak_data.git#' \
            -e 's/^#\?ENGINE_BRANCH=.*/ENGINE_BRANCH=main/' /etc/kayak/deploy.env
sudo env KAYAK_DEPLOY_CONF=/etc/kayak/deploy.env deploy/kayak-deploy.sh \
  --engine-ref <40hex> --dataset-ref <40hex> --stage-only        # prints the release dir → R
R=/opt/kayak/releases/<id>

# Phase 1 — apply the cutover config to the FILES only; DO NOT reload nginx/FPM yet
sudo install -d /run/kayak-serving
sudo $R/venv/bin/levels render-serving --out-dir /run/kayak-serving
sudo sed -i "s#^[[:space:]]*root .*/public_html;#$(cat /run/kayak-serving/nginx-levels-docroot.conf)#" /etc/nginx/snippets/levels-common.conf
sudo nginx -t                                          # validates the FILE; running nginx unchanged
sudo sed -i "s#^[[:space:]]*php_admin_value\[open_basedir\].*#$(cat /run/kayak-serving/fpm-open-basedir.conf)#" /etc/php/8.4/fpm/pool.d/kayak.conf
sudo systemctl stop 'kayak-*.timer'                    # BEFORE re-pointing — a re-pointed unit must not fire pre-activation
sudo $R/venv/bin/levels render-units --out-dir /etc/systemd/system    # WARNs "current doesn't exist yet" — expected
sudo systemctl daemon-reload
sudo -u <svc> sed -i 's#^OUTPUT_DIR=.*#OUTPUT_DIR=/var/cache/kayak/docroot#' /home/<svc>/.config/kayak/.env
sudo setfacl -R -m u:www-data:rX /var/cache/kayak/docroot; sudo setfacl -R -d -m u:www-data:rX /var/cache/kayak/docroot
sudo sed -i -e 's/^#\?SERVING_CUTOVER=.*/SERVING_CUTOVER=yes/' \
  -e 's,^#\?KAYAK_NGINX_DOCROOT_CONF=.*,KAYAK_NGINX_DOCROOT_CONF=/etc/nginx/snippets/levels-common.conf,' \
  -e 's,^#\?KAYAK_FPM_POOL=.*,KAYAK_FPM_POOL=/etc/php/8.4/fpm/pool.d/kayak.conf,' /etc/kayak/deploy.env
# the install-config.sh FPM/nginx-reload step is SKIPPED here — the pool + runtime-config
# already exist on a running host, and a reload now would serve the empty new docroot.

# Phase 2 — activate (gate passes on the FILES; migrate runs on the live DB; builds the new docroot)
sudo env KAYAK_DEPLOY_CONF=/etc/kayak/deploy.env deploy/kayak-deploy.sh \
  --engine-ref <40hex> --dataset-ref <40hex>
# (still serving public_html — the activation built /var/cache/kayak/docroot but didn't touch nginx/FPM)

# Phase 3 — the graceful flip to the new docroot. RELOAD both, not restart: a
# `systemctl restart php8.4-fpm` drops all workers and has a brief not-ready window
# that 404/502s live PHP requests; `reload` is graceful (in-flight requests finish)
# AND re-reads the pool config, so it applies the new open_basedir with no outage
# (verified on the VM — reload picks up the open_basedir change, restart raced a 404).
sudo systemctl reload nginx          # now roots /var/cache/kayak/docroot (built)
sudo php-fpm8.4 -t                    # validate the pool config before reloading
sudo systemctl reload php8.4-fpm      # graceful — applies the new open_basedir, no outage
```

Verify: homepage + `description.php?h=1` 200 from the new docroot
(`nginx -T | grep 'root /var/cache'`); `kayak-audit-gauges`'s ExecStart is now
`levels audit-gauges` (the #191 fix lands *via the cutover*, since
`scripts/deploy.sh` never reinstalled the unit). The old `public_html` is now
unused — leave it as a fallback for the soak, then prune it per **Post-cutover
cleanup** below (note `.venv` is *not* unused — the shell consumers still need it).
Future deploys go through `kayak-deploy.sh`, not `scripts/deploy.sh`.

> **If the activation FAILS** (bad `HEALTH_URL`, build error, …) — validated by a
> rollback rehearsal: the deployer restores the DB backup + config and aborts the
> release (removes `current`, leaves `/opt/kayak/maintenance`, consumers stopped),
> and **the old `public_html` keeps serving** because Phase 1–2 never reloaded
> nginx/FPM — a failed cutover does NOT take the site down. The config *files*
> already point at the new docroot, so a stray nginx reload would switch to it.
> **Recover by fixing the cause and retrying:** `sudo rm -f /opt/kayak/maintenance`
> then re-run the Phase-2 `kayak-deploy.sh` — the retry re-activates cleanly and the
> Phase-3 flip serves the new docroot (confirmed on the VM, DB intact). To abort
> entirely instead: revert the nginx-`root` / FPM-`open_basedir` seds + reload, `rm`
> the drop-ins + `daemon-reload`, and restart the timers.

### Post-cutover cleanup (one-time, after a few days' soak)

The cutover deliberately leaves the old single-tree artifacts in place as a
fallback. Once the paired release has served cleanly for a few days (homepage +
DB-backed PHP 200 from the cache docroot, the hourly pipeline rebuilding it, a
deploy + a rollback both exercised), the **superseded** artifacts can be removed.
These are the items whose disposition was "retired" / "relocated" in the mapping
table above — never the mutable state or the still-live consumers:

```bash
# 1. the old served docroot — nginx now roots /var/cache/kayak/docroot
rm -rf /home/<svc>/public_html

# 2. the two in-tree caches that relocated to /var/cache/kayak/{map-layers,gauge-metadata}
#    (stale after the first post-cutover fetch-osmb / audit-gauges run writes the new dirs)
rm -rf /home/<svc>/kayak/var/osmb /home/<svc>/kayak/Gauge-metadata-cache

# 3. the legacy deploy path — confirm nothing still triggers it, then it's inert
#    (the pre-flight already disabled any cron/timer that git-pulls main or runs
#    scripts/deploy.sh; scripts/deploy.sh stays in the tree, just unused)
```

**Do NOT remove yet — these are still live, not leftovers:**
- `~/kayak` (the checkout) and `~/.venv` — the cutover re-points only the *engine*
  units (`pipeline`, `decimate`, `editor-retention`, `status`, `fetch-osmb`,
  `audit-gauges`). The shell-script units (`kayak-recap`, `-heartbeat`,
  `-config-drift`, `-healthcheck`, `-cert-expiry`, `-backup-*`) still `ExecStart`
  from `~/kayak/{scripts,systemd}/`, and `kayak-recap.sh` still runs
  `~/.venv/bin/python3`. Retiring `~/kayak`/`~/.venv` is the deferred
  genericization (move those consumers into the release), **not** a soak-cleanup.
- `~/DB`, `~/var`, `~/logs`, `~/backups`, `~/.config/kayak`, `~/kayak_data` —
  mutable state / unchanged-by-design (and `~/kayak_data` is the deployer's
  snapshot source). Leave them.

### Ongoing disk hygiene (what accumulates per release)

Each `kayak-deploy.sh` stages a fresh immutable release (a full venv + a dataset
snapshot + the wheel/locks), so disk grows with deploy churn. Most of it is
self-bounding; the one manual case is a *failed* deploy:

| What grows | Bound | Action |
|---|---|---|
| `/opt/kayak/releases/<id>/` | **automatic** — the deployer prunes to `KAYAK_KEEP_RELEASES` (default **5**) newest, *never* deleting `current` or the previous (rollback needs it), after each successful activation | none normally; lower `KAYAK_KEEP_RELEASES` if the 40 GB VPS gets tight (each release ≈ one venv + dataset snapshot), or `ls -1dt /opt/kayak/releases/*/` to inspect |
| `/opt/kayak/.staging/` | **automatic on success** — the EXIT-trap `rm -rf`s the scratch (incl. the ~650 MB pre-activate DB backup) when the deploy succeeds | **manual after a FAILED/rolled-back deploy**: the deployer keeps the scratch on purpose (`CLEAN_SCRATCH=0`) so the `pre-activate.db` backup survives for recovery. Once recovered/verified, `sudo rm -rf /opt/kayak/.staging/<leftover>` |
| `/opt/kayak/maintenance` | removed at the end of a successful activation | only lingers after an aborted deploy — `sudo rm -f` it as part of the retry/recover step (already in the rollback note) |
| `~/backups/` | governed by the existing `kayak-backup-{hourly,weekly,offsite}` units — **unchanged by the cutover** | their own retention applies; out of scope here beyond noting they keep running from `~/kayak/systemd/` |

So under steady state nothing needs hand-pruning: releases self-bound at 5+2 and
the scratch self-cleans. The only recurring manual task is sweeping a failed
deploy's `.staging` leftover *after* you've confirmed the DB restored from it.

## Resolved findings (were open items)

- **DB bootstrap:** `init-db` + `sync-metadata` + `import-metadata` via the staged
  release venv, before the first activation (step 4).
- **§6 ACLs are mandatory** — home ACLs (503 on DB-backed PHP) + the docroot
  default ACL (403 on freshly built pages).
- **Virgin-host failure path works** (step 7 note).
- **Scratch must be real disk** — defaults to `$ROOT/.staging`, not `/tmp` (tmpfs).
- **Renderers replace the hand-crafted step 5** (#193–#196): `render-units` for the
  6 engine drop-ins (audit-gauges promoted to an engine consumer, #191),
  `render-serving` for the nginx root + FPM open_basedir, and the gate sources its
  must-run-from-current set from `render-units --list-units` (no `KAYAK_HOST_UNITS`).
- **Docroot is the #3 shared cache** `/var/cache/kayak/docroot`, not inside the
  release.

## Still open

- **Execute the live WKCC cutover.** Both the virgin install and the running-host
  migration (above) are VM-validated end-to-end; what's left is running the
  migration on the real host. Snapshot/backup first; the rollback path (a failed
  activation restores the DB + leaves the host in maintenance) is the safety net,
  and the old `public_html`/`.venv` stay as a manual fallback.
- **Genericization (deferred):** the vhost `server_names` type and the hardcoded
  `/var/www/certbot` ACME root become `host.yaml` knobs only for a truly non-WKCC
  install (keep-current-then-flip leaves them at WKCC values today).
