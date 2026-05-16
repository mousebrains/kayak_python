# Pre-cutover preflight — 2026-05-10

Pre-production review of the kayak repo + live system at `5.78.185.66`
ahead of the 2026-05-20 cutover from `levels-test.wkcc.org` to the
canonical `https://levels.wkcc.org`.

Driven by the read-only sysinfo dump produced by
`scripts/sysinfo-for-review.sh` (2026-05-10 19:37 PDT, 216 KB). Repo
side was cross-checked against the live `/etc/` tree via the script's
drift section. Every tracked artifact under `deploy/`, `conf/`, and
`systemd/` was byte-identical to its `/etc/` counterpart — no
unintended drift on what we ship.

This document is the closing record. Each P0/P1 has a commit on `main`
or an outstanding action.

---

## Resolved this pass (Phases A + B, commits 6abdc48 + 07e2f33)

### P0

- **kayak-backup.service same-day gzip collision.** The 2026-05-10
  11:44 run failed because gzip refused to overwrite an existing
  `kayak-20260510.db.gz` from the 03:15 nightly. Resolved by switching
  the backup filename to second-resolution UTC stamps
  (`backup-YYYYMMDDTHHMMSSZ.db.gz`) so same-day re-runs cannot
  collide, plus a `rm -f` of any leftover uncompressed `.db` from a
  prior failed run before today's gzip step. *(Resolved before this
  pass; verified during it.)*
- **Cert SAN does not cover `levels.wkcc.org`.** Out of scope for
  this pass — waiting on ClubExpress CNAME for the cutover-day SAN
  expansion. See *Open items*.

### P1

- **No swap on a 1.9 GB box.** 4 GB swap file added + persisted in
  `/etc/fstab`, with `vm.swappiness=10` via
  `deploy/sysctl.d/90-swap.conf`. `free -h` now reports `Swap: 4.0Gi`.
- **PHP-FPM `pm.max_requests = 0`.** Set to `500` in
  `deploy/kayak-fpm-pool.conf`. Live `php-fpm8.4 -tt` confirms
  `pm.max_requests = 500`.
- **`MAINTAINER_EMAIL` Python/PHP asymmetry.** `src/kayak/config.py:38`
  now reads `MAINTAINER_EMAIL` from the env first, matching
  `php/includes/auth.php::maintainer_emails()`. A systemd
  `Environment=MAINTAINER_EMAIL=…` override now reaches both layers.
- **fail2ban sshd jail missing the Timeout-before-auth pattern.**
  `deploy/fail2ban/jail.local` `[sshd]` block now has `mode =
  aggressive`. Live `fail2ban-client status sshd` shows the jail
  still active; Total-failed counter advances under the new pattern.
- **nginx-editor-env.conf still points at the soak host.** Known
  cutover-day flip — see *Open items*.

### P2

- **`ssl_stapling` warning at every nginx reload.** Removed from
  `deploy/levels` and `conf/levels.nginx`; explanatory comment left
  in place so a future contributor doesn't re-add it. Confirmed
  silenced on the 20:52 reload.
- **nginx config naming drift.** Repo had `nginx-kayak-log-format.conf`
  + `nginx-ratelimit.conf`; live installed as `kayak-log-format.conf`
  + `ratelimit.conf`. Repo files renamed; SETUP.md install step
  updated.
- **SETUP.md `/home/tpw/` legacy.** 21 occurrences across §2, §3, §8,
  and the storage-box example replaced with `/home/pat/`. The legacy
  `tpw` host is gone.
- **SETUP.md §8 timer count.** Title was "Systemd timers (pipeline +
  decimate)"; actual install brings up six timers + the
  OnSuccess-chained offsite backup. §8 rewritten with the full list,
  schedules, and `RandomizedDelaySec` note.
- **Empty `kayak.db` at repo root (April 17 placeholder).** Removed
  by the user out-of-band.
- **`hardening/` scratch directory.** Reconciled and removed. Stale
  duplicates of `deploy/fail2ban/` and `deploy/sshd_config.d/` were
  deleted; uniques (`nftables.conf`, `msmtprc.example`, `msmtp-aliases`,
  `sysctl.d/`, `apt.conf.d/`) were promoted into `deploy/`. Three
  additional sysctl drop-ins that lived only on the live box
  (`90-swap.conf`, `92-local-hardening.conf`, `99-hardening.conf`)
  were also brought into the repo. SETUP.md gains §13-17 for these
  pieces.
- **`opus.review.md` removed.** All 43 items shipped Phases 1-25;
  doc no longer load-bearing. Commit `87c279c`.

---

## Open items (post-this-pass)

### Cutover-day-and-after

1. **Cert SAN expansion** — at T0+3 via HTTP-01 (`certbot --nginx --expand`)
   per `DNS.CHANGEOVER.md` Phase C. The bridge cert at
   `/etc/nginx/certs/levels.wkcc.org.{cert,privkey}` (installed
   2026-05-14, post-dating this preflight) handles the cutover window
   so there's no SSL handshake race to dodge. The DNS-01 acquisition
   path the original preflight pointed at has been retired.

2. **`$site_url` flip in `nginx-editor-env.conf`** at T0+3 (or
   whenever the new CNAME has propagated and traffic is on
   `levels.wkcc.org` for real). The file already documents the flip
   in its header.

### Investigated and dropped

- **CAA pin on `wkcc.org`.** Not applicable in the cutover architecture:
  `levels.wkcc.org` becomes a CNAME to `levels.mousebrains.com`, and
  RFC 1034 forbids any other RR type alongside a CNAME — so CAA can't
  live at that name. Pinning at the `wkcc.org` apex would constrain
  `www.wkcc.org`'s own (non-Let's-Encrypt) issuer. The protection
  already lives where it matters: `mousebrains.com` apex CAA pins to
  LE/DigiCert/Google/ssl.com, covering the cert's primary CN.

### Cosmetic / can-defer-past-cutover

- **`wkcc.org` DMARC at `p=none`.** Passive monitor; nothing breaks.
  Long-term, move to `p=quarantine` → `p=reject` after watching
  `rua=` reports for unknown senders. Not a launch concern.
- **CSP report log harvest cadence.** `/home/pat/logs/csp.log` was
  empty at sysinfo time. No incidents.

---

## What's solid — confirmed by the dump

- All six kayak timers active and triggering on schedule. Pipeline
  ran 22 min before the dump; healthcheck 49 min before; both green.
- nftables ruleset clean (default-drop input, established/related
  accept, ICMP allowed, SSH rate-limited 3/min/IP, HTTP/HTTPS open).
  `f2b-table` adds dynamic ban sets at chain priority -1 so banned
  IPs are rejected before reaching nginx/sshd.
- fail2ban has 8 jails active: `sshd`, `nginx-http-auth`,
  `nginx-botsearch`, `nginx-limit-req`, `nginx-malicious`,
  `nginx-default-block`, `nginx-edit-auth`, `nginx-editor-auth`.
  Currently 153 IPs banned in aggregate, ~600 total since
  installation. No false-positive complaints.
- Cert on `levels.mousebrains.com` is ECDSA, 80 days from expiry,
  auto-renewing via `/etc/cron.d/certbot`.
- msmtp + Gmail relay: heartbeat email at 06:00 today succeeded
  (`exitcode=EX_OK`), audit-gauges digest at 14:24 succeeded.
- SQLite: `integrity_check` and `quick_check` both `ok`. 563 MB
  database, WAL mode, 38 GB disk 36% used. Plenty of room.
- Repo `deploy/`+`conf/`+`systemd/` byte-identical to every tracked
  `/etc/` counterpart (verified by the dump's drift section).
- All 6 fail2ban + nginx + php-fpm + sshd config changes from this
  pass have been deployed live and verified by post-reload status.

---

## Files produced this pass

- `scripts/sysinfo-for-review.sh` — read-only sysinfo dump.
- `docs/preflight-2026-05-10.md` — this file.
- Plus the eleven edits across `deploy/`, `conf/`, `src/kayak/`,
  `systemd/`, and `.gitignore` in commits `6abdc48` and `07e2f33`.

## Next pass (if needed before cutover)

Two unrun passes from the plan, parked until they're useful:

- **Pass 5** — walk `DNS.CHANGEOVER.md` end-to-end against the live
  state and produce a single numbered cutover-day checklist.
  Worth running ~48 h before cutover, after the bridge cert is in
  place (Phase A — landed 2026-05-14) and the ClubExpress A→CNAME
  ticket is queued (Phase B).
- **Pass 6** — curl every URL in `sitemap.xml` from a third-party
  host (not the box itself) and verify 2xx + correct cache-control
  + OG meta intact. Worth running ~24 h before cutover as a final
  regression check.
