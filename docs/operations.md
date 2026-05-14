# Operations runbook

Quick reference for the most common things you'll need to do on the
live host. The bigger picture (architecture, SLOs, on-call rotation)
will land here in increments — see `docs/PLAN_production_discipline.md`
Phase 4.2 for the planned expansion.

This doc lives at `/home/pat/kayak/docs/operations.md` on the live host
itself. Reading it locally is part of the recovery loop: when something
breaks, the steps shouldn't depend on a network round-trip to GitHub.

## Overview

| What | Where |
|---|---|
| App | Python (`src/kayak/`) + PHP (`php/`); two-layer architecture, see `CLAUDE.md`. |
| Database | SQLite at `/home/pat/DB/kayak.db` (WAL mode). |
| Web | nginx + PHP-FPM 8.4. Three vhosts: `levels.mousebrains.com`, `levels-test.wkcc.org`, `levels.wkcc.org`. |
| Cert | Let's Encrypt 2-SAN at `/etc/letsencrypt/live/levels.mousebrains.com/` + bridge cert at `/etc/nginx/certs/levels.wkcc.org.{cert,privkey}` (the latter only during the DNS cutover window; see `DNS.CHANGEOVER-fastpath.md`). |
| Scheduled work | 12 systemd timers — pipeline, backups, cert health, decimation, etc. See `deploy/SETUP.md` §timer schedule. |
| Monitoring | healthchecks.io (heartbeats), ntfy.sh (push), msmtp → Gmail (email). |
| Backups | `/home/pat/kayak/backups/` (hourly + weekly local) + Google Drive crypt (weekly off-site). |

## Health endpoints / monitoring map

| Signal | Path / unit | Configured cadence |
|---|---|---|
| Public homepage | `https://levels.mousebrains.com/` | Better Stack uptime pinger |
| Pipeline heartbeat | `kayak-pipeline.service` → `${HC_PIPELINE}` | Every hour at :12 |
| Hourly backup heartbeat | `kayak-backup-hourly.service` → `${HC_BACKUP_HOURLY}` | Every hour at :38 |
| Data-freshness | `kayak-healthcheck.service` → `${HC_HEALTHCHECK}` | Every hour at :45 |
| Cert health probe | `kayak-cert-expiry.service` → `${HC_CERT_EXPIRY}` | Daily at 06:30 |
| Cert renewal dry-run | `kayak-cert-renewal-test.service` → `${HC_CERT_RENEWAL_TEST}` | Weekly Mon 04:15 |
| Config drift | `kayak-config-drift.service` → `${HC_CONFIG_DRIFT}` | Weekly Sun 05:30 |
| Mail-path liveness | `kayak-heartbeat.service` → `${HC_HEARTBEAT}` | Weekly Sun 06:00 |

All HC_ URLs live in `~/.config/kayak/.env` (chmod 600). The
`OnFailure=kayak-notify-failure@%n.service` template on every unit
routes errors to email + ntfy.

## Backup + restore

### Where backups live

- **Hourly local:** `/home/pat/kayak/backups/hourly-*.db.gz` (24 newest).
  Filename is the UTC second-resolution timestamp.
- **Weekly local:** `/home/pat/kayak/backups/backup-*.db.gz` (4-copy
  retention: newest plus positions 1/3/5 in the sorted list).
- **Weekly off-site:** Google Drive at `gdrive-crypt:` (rclone-encrypted;
  26-copy retention — ~6 months at one per week). See
  `docs/offsite-backup.md` for the rclone config and recovery procedure.

### Restore from local hourly (RPO ≤ 1 hour)

```bash
# 1. Identify the snapshot to restore (newest by default; pick by mtime/name otherwise)
ls -lht /home/pat/kayak/backups/hourly-*.db.gz | head

# 2. Stop the pipeline + decimate so they don't write during the swap
sudo systemctl stop kayak-pipeline.timer kayak-decimate.timer \
    kayak-backup-weekly.timer kayak-backup-hourly.timer
sudo systemctl stop kayak-pipeline.service  # in case one is mid-run

# 3. Archive the broken DB before overwrite (so the rollback path stays open)
sqlite3 /home/pat/DB/kayak.db ".backup /tmp/kayak-broken-$(date -u +%Y%m%dT%H%M%SZ).db" || true
mv /home/pat/DB/kayak.db /tmp/kayak-pre-restore-$(date -u +%Y%m%dT%H%M%SZ).db
rm -f /home/pat/DB/kayak.db-wal /home/pat/DB/kayak.db-shm

# 4. Decompress the chosen backup into place
gunzip -c /home/pat/kayak/backups/hourly-<UTC-stamp>.db.gz > /home/pat/DB/kayak.db
chmod 660 /home/pat/DB/kayak.db
chown pat:www-data /home/pat/DB/kayak.db  # if the DB came back owned by root

# 5. Integrity check before re-enabling the pipeline
sqlite3 /home/pat/DB/kayak.db 'PRAGMA integrity_check;'  # expect: ok

# 6. Re-enable timers
sudo systemctl start kayak-pipeline.timer kayak-decimate.timer \
    kayak-backup-weekly.timer kayak-backup-hourly.timer

# 7. Watch the first pipeline run pick it up
sudo systemctl start kayak-pipeline.service
sudo journalctl -u kayak-pipeline --since '5 minutes ago' -n 40 --no-pager
```

The `pre-restore-*.db` file in `/tmp/` is your safety net: if something
goes wrong with the restored DB, rename it back over `kayak.db`, restart
the timers, and you're back to where you started.

### Restore from off-site

See `docs/offsite-backup.md`. The procedure is essentially the same as
above, except step 4 pulls from `gdrive-crypt:` via rclone first.

## Recovering from a partial `@no_transaction` migration

Migrations marked `-- @no_transaction` at the top of the SQL file run
*outside* the runner's `engine.begin()` wrapper. The reason is that
some structural changes (notably the table-rebuild pattern in migration
`0012_reach_name_partial_unique.sql`) need `PRAGMA foreign_keys = OFF`
to be effective, and SQLite silently ignores that pragma mid-transaction.

The cost: if a `@no_transaction` migration fails partway through, the
intermediate state stays on disk. The runner won't bump
`schema_migrations.version` because completion wasn't signalled, but
the actual schema may be partially mutated.

**How to detect.** The classic symptom is one or more tables named
`<original>_new` (e.g. `reach_new`) in the live DB:

```bash
sqlite3 /home/pat/DB/kayak.db '.schema' | grep -E '^CREATE TABLE [a-z_]+_new'
```

If that prints anything, the rebuild was interrupted before the
`DROP TABLE original; ALTER TABLE original_new RENAME TO original;`
swap completed.

**Recovery procedure (illustrated with 0012's `reach` rebuild):**

1. **Snapshot the DB before doing anything.** If the recovery itself
   blows up, you want the broken state preserved for forensics.

   ```bash
   sqlite3 /home/pat/DB/kayak.db \
       ".backup /tmp/kayak-pre-recovery-$(date -u +%Y%m%dT%H%M%SZ).db"
   ```

2. **Check the original table's schema.** Compare against the post-
   migration shape in the `_new` table:

   ```bash
   sqlite3 /home/pat/DB/kayak.db '.schema reach reach_new'
   ```

3. **Decide which way to recover** based on what you find:

   - **Case A: `_new` was created but never populated** (CREATE TABLE
     succeeded, INSERT INTO ... SELECT failed). Drop `_new`; the
     original is intact:

     ```bash
     sqlite3 /home/pat/DB/kayak.db 'DROP TABLE reach_new;'
     ```

   - **Case B: `_new` has the rows but the swap (`DROP TABLE reach`,
     `ALTER TABLE reach_new RENAME TO reach`) never happened.** Finish
     the swap manually:

     ```bash
     sqlite3 /home/pat/DB/kayak.db <<'SQL'
     PRAGMA foreign_keys = OFF;
     DROP TABLE reach;
     ALTER TABLE reach_new RENAME TO reach;
     PRAGMA foreign_keys = ON;
     SQL
     ```

     Then re-run any indexes/triggers the migration created on the new
     table (compare against the migration SQL file).

   - **Case C: original is gone, `_new` is the only copy.** Treat as
     Case B but verify integrity first: `PRAGMA integrity_check;`
     before the rename.

4. **Confirm `schema_migrations` is consistent with the on-disk state.**

   ```bash
   sqlite3 /home/pat/DB/kayak.db 'SELECT * FROM schema_migrations;'
   ```

   If the failed migration's version appears, the recovery should mirror
   what that migration claimed to do (Case B or C above). If it doesn't
   appear, the runner believes the migration never ran — re-running
   `levels migrate` will retry it, which works if you've cleaned up to
   Case A's state (no `_new` table).

5. **Re-run the migration runner to apply any pending migrations:**

   ```bash
   /home/pat/.venv/bin/levels migrate
   ```

6. **Sanity-check:** the test suite's schema-parity check (T2.3 of
   `docs/PLAN_pre_release_followup.md` — pending) will compare
   migration-built vs ORM-built shape; until then, eyeball
   `.schema | wc -l` against a fresh `levels init-db`'s output to
   confirm parity.

## Cert renewal

See `DNS.CHANGEOVER-fastpath.md` for the active cert lifecycle plan
(bridge cert during the DNS cutover window → `certbot --expand` once
DNS propagates). The standard renewal path (post-cutover) is the
OS-managed `certbot.timer` running twice daily; `kayak-cert-expiry.timer`
+ `kayak-cert-renewal-test.timer` (P0.2 of the audit follow-up plan)
provide the alerting layer if `certbot.timer` ever silently breaks.

## Pipeline failure triage

When `kayak-pipeline.service` exits nonzero:

1. **Look at the journal first** — it has the full per-step output:

   ```bash
   sudo journalctl -u kayak-pipeline --since '1 hour ago' --no-pager
   ```

   The pipeline prints a clear `============ Running: <step> ============`
   banner per step plus the exception traceback when one fails. After
   QW.5, downstream steps short-circuit if `fetch` or `fetch-usgs-ogc`
   fails, so the failure point is usually obvious from the last
   `Running: …` banner before the SystemExit.

2. **Common causes by step:**

   - `fetch` — network out to a gov't agency feed; check
     `journalctl -u kayak-pipeline` for which URL timed out. Some feeds
     (NWRFC textplot, USACE CDA) drop briefly during maintenance — wait
     and retry rather than chasing root cause.
   - `fetch-usgs-ogc` — USGS OGC API; same as above but specifically the
     USGS endpoint.
   - `update-gauge-cache` — DB-internal; usually means a parser stored
     observations with a missing `gauge_source` link. Check the
     `audit-gauges` weekly report for stale source-gauge mappings.
   - `calculator` — a `calc_expression` references a gauge that no
     longer has a current observation. Per `memory/feedback_calc_orphans`,
     calc refs resolve via gauge name; verify the referenced gauge still
     exists.
   - `build` — usually a Python exception from a schema change that
     didn't propagate to the build code. Re-run `pytest tests/` to
     surface.

3. **Run the failing step on its own** to iterate faster than waiting
   for the next timer fire:

   ```bash
   sudo systemctl start kayak-pipeline.service
   # OR run a single step directly (no need for sudo if running as pat):
   /home/pat/.venv/bin/levels fetch
   /home/pat/.venv/bin/levels build
   ```

4. **If the failure is upstream (the feed is down), use
   `--continue-on-error`** to force the pipeline to keep going so the
   public HTML stays fresh from the partial fetch:

   ```bash
   /home/pat/.venv/bin/levels pipeline --continue-on-error
   ```

   This opts out of QW.5's fail-fast and runs every step regardless.

## Config drift

The weekly `kayak-config-drift.service` (T1.2 of the audit follow-up
plan) diffs every tracked file under repo `conf/`, `deploy/`, `systemd/`
against its installed `/etc/` location. When it fires OnFailure:

```bash
sudo journalctl -u kayak-config-drift --since '8 days ago' --no-pager
```

Each drift is reported as a unified diff. For each item, decide which
side is canonical:

- **Repo is canonical** (the usual case): `sudo install -m <mode>
  /home/pat/kayak/<path> /etc/<path>` then `daemon-reload` / `reload`
  / `restart` whichever service owns that file.
- **Live is canonical** (someone edited /etc/ on the fly for an
  emergency fix): copy the live file back into the repo, commit with
  the rationale.

A clean run prints `Checked N file(s): N match, 0 differ, 0 missing`
and exits 0.

## Quick reference: stop everything before a destructive operation

```bash
# Stop all kayak-* timers + their services
systemctl list-timers --all 'kayak-*' --no-pager | awk '/^[A-Z]/ {print $NF}' \
    | xargs -I{} sudo systemctl stop {}

# (the .service names are inferred from the timer names by systemd)
```

## When in doubt

- Check `journalctl -u <unit> --since '1 hour ago'`.
- Backups are cheap to make and free to keep — do one before any
  destructive operation.
- The drift detector is the canonical record of what should be on disk
  vs what is. A clean run is a load-bearing baseline.
