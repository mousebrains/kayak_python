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
| Cert | Let's Encrypt 3-SAN at `/etc/letsencrypt/live/levels.mousebrains.com/` covering `levels.mousebrains.com`, `levels-test.wkcc.org`, `levels.wkcc.org`. Renewed via certbot's nginx (HTTP-01) authenticator. |
| Scheduled work | 15 systemd timers — pipeline, backups, cert health, decimation, status page, OSMB fetch, etc. See `deploy/SETUP.md` §timer schedule. |
| Monitoring | healthchecks.io (heartbeats), ntfy.sh (push), msmtp → Gmail (email). Public status page at <https://status.mousebrains.com> (Better Stack hosted, CNAME → `statuspage.betteruptime.com`). |
| Backups | `/home/pat/backups/` (hourly + weekly local) + Google Drive crypt (weekly off-site). |
| Operator dashboard | `/_internal/status` (maintainer-only) — disk/memory, backups, TLS-cert expiry, failed `kayak-*` jobs, and 4 h traffic buckets. Regenerated nightly by `kayak-status.timer` (03:30) into `var/status.html`. |

## Routine code deploy

`scripts/deploy.sh` codifies the common path: pull main, refresh
Python deps only if `pyproject.toml` changed, apply pending migrations,
rebuild static HTML. Run as `pat` from `/home/pat/kayak`:

```bash
cd /home/pat/kayak
./scripts/deploy.sh
```

It exits non-zero on any sub-step failure (`set -e`). It does NOT
touch `/etc/systemd/system/`, `/etc/nginx/`, or anything else
root-owned — those rare changes need the manual diff-then-`sudo cp`
flow (and a `sudo nginx -t` / `sudo systemctl daemon-reload` after).
When the pulled commits touch `systemd/`, `conf/sites/`, or
`conf/snippets/`, the script prints a NOTICE listing the changed
paths so you know to apply them by hand.

Rollback: `git checkout <prev-sha> && ./scripts/deploy.sh`. Note that
data migrations are forward-only — a code rollback that depends on
old schema needs a fresh migration to undo the change first.

## Releases

`scripts/release.sh` prepares a release commit (bumps
`pyproject.toml`, flips `CHANGELOG.md`'s `[Unreleased]` heading to a
dated `[X.Y.Z]` section, commits). It does **not** create the git
tag — that step is intentionally manual so the maintainer controls
when the version goes out.

```bash
cd /home/pat/kayak
scripts/release.sh 1.0.1            # or 1.1.0 / 2.0.0 — explicit semver
# Script prints the tag command; copy-paste when ready:
git tag -a v1.0.1 -m 'release v1.0.1'
git push origin v1.0.1
git push origin main
```

Pre-flight checks (refuses if any fail):
- Working tree must be clean (no uncommitted edits).
- Tag `vX.Y.Z` must not already exist locally or on origin.
- `CHANGELOG.md` must have an `## [Unreleased]` heading to flip.

**Pre-v1.0.0:** direct-to-`main` commits + an immediate
`scripts/deploy.sh` push. **Post-v1.0.0:** every change starts on a
feature branch, lands via PR, merges, then deploys. The v1.0.0 tag
is the transition marker.

To check what's running on prod against the tag set:

```bash
cd /home/pat/kayak
git fetch --tags
git describe --tags --exact-match HEAD 2>/dev/null || git describe --tags HEAD
```

A future deploy.sh enhancement will write the deployed tag to
`/etc/kayak/VERSION` (production-discipline Tier 3 follow-up) so the
check becomes a privilege-free `cat`. For now, `git describe` is the
authoritative answer.

See § Rollback below for the SHA-based rollback recipe; once tags
exist, prefer `git checkout v<prev-version>` over a raw SHA — same
mechanics, more readable in the audit log.

## Health endpoints / monitoring map

| Signal | Path / unit | Configured cadence |
|---|---|---|
| Public status page | <https://status.mousebrains.com> | Better Stack hosted; surfaces the uptime + content-keyword monitors |
| Health snapshot JSON | `https://levels.wkcc.org/status.json` (rewrites to `php/status.php`; also served from the two other vhosts) | On-demand; `Cache-Control: no-cache, max-age=10`. Per-agency freshness rollup + per-status gauge counts |
| Internal dashboard | `https://levels.wkcc.org/_internal/` (`php/_internal/index.php`) | On-demand; maintainer-only via `editor_session`. Per-source freshness, recent CSP violations, aggregate counts, build mtime, DB size. `levels.mousebrains.com` returns 404 (login flow targets `levels.wkcc.org` per `SITE_URL`, so the dashboard host has to match); `levels-test.wkcc.org` 301s wholesale to `levels.wkcc.org` (`conf/sites/levels-test-wkcc-org`, since 2026-05-19) |
| Public homepage | Better Stack monitor on `https://levels.wkcc.org/` | HEAD/GET 3-min interval |
| Pipeline heartbeat | `kayak-pipeline.service` → `${HC_PIPELINE}` | Every hour at :12 |
| Hourly backup heartbeat | `kayak-backup-hourly.service` → `${HC_BACKUP_HOURLY}` | Every hour at :38 |
| Data-freshness | `kayak-healthcheck.service` → `${HC_HEALTHCHECK}` | Every hour at :45 |
| Cert health probe | `kayak-cert-expiry.service` → `${HC_CERT_EXPIRY}` | Daily at 06:30 |
| Cert renewal dry-run | `kayak-cert-renewal-test.service` → `${HC_CERT_RENEWAL_TEST}` | Weekly Mon 04:15 |
| Config drift | `kayak-config-drift.service` → `${HC_CONFIG_DRIFT}` | Weekly Sun 05:30 |
| Mail-path liveness | `kayak-heartbeat.service` → `${HC_HEARTBEAT}` | Weekly Sun 06:00 |
| Weekly recap | `kayak-recap.service` → `${HC_RECAP}` | Weekly Mon 07:00 |
| Operator log analytics | `levels analyze-logs {release\|humans\|chunked}` | On-demand (no timer). Release post-mortem + visitor breakdown — see `docs/PLAN_logs_analyze_migration.md` |

All HC_ URLs live in `~/.config/kayak/.env` (chmod 600). The
`OnFailure=kayak-notify-failure@%n.service` template on every unit
routes errors to email + ntfy. The Better Stack uptime monitor
also pushes notifications to the same ntfy topic via a webhook
destination, so a frontend outage and a unit failure surface
through the same channel.

## Backup + restore

### Where backups live

- **Hourly local:** `/home/pat/backups/hourly-*.db.gz` (24 newest).
  Filename is the UTC second-resolution timestamp.
- **Weekly local:** `/home/pat/backups/backup-*.db.gz` (4-copy
  retention: newest plus positions 1/3/5 in the sorted list).
- **Weekly off-site:** Google Drive at `gdrive-crypt:` (rclone-encrypted;
  26-copy retention — ~6 months at one per week). See
  `docs/offsite-backup.md` for the rclone config and recovery procedure.

### Restore from local hourly (RPO ≤ 1 hour)

```bash
# 1. Identify the snapshot to restore (newest by default; pick by mtime/name otherwise)
ls -lht /home/pat/backups/hourly-*.db.gz | head

# 2. Stop the pipeline + decimate so they don't write during the swap
sudo systemctl stop kayak-pipeline.timer kayak-decimate.timer \
    kayak-backup-weekly.timer kayak-backup-hourly.timer
sudo systemctl stop kayak-pipeline.service  # in case one is mid-run

# 3. Archive the broken DB before overwrite (so the rollback path stays open)
sqlite3 /home/pat/DB/kayak.db ".backup /tmp/kayak-broken-$(date -u +%Y%m%dT%H%M%SZ).db" || true
mv /home/pat/DB/kayak.db /tmp/kayak-pre-restore-$(date -u +%Y%m%dT%H%M%SZ).db
rm -f /home/pat/DB/kayak.db-wal /home/pat/DB/kayak.db-shm

# 4. Decompress the chosen backup into place
gunzip -c /home/pat/backups/hourly-<UTC-stamp>.db.gz > /home/pat/DB/kayak.db
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
   `docs/done/PLAN_pre_release_followup.md` — pending) will compare
   migration-built vs ORM-built shape; until then, eyeball
   `.schema | wc -l` against a fresh `levels init-db`'s output to
   confirm parity.

## Schema decisions

This section captures the per-feature outcomes from T3.5 of
`PLAN_pre_release_followup.md` (architecture audit ARCH-H10), so a
future maintainer auditing the schema can re-derive "why is X still
here" without re-reading migration 0022's commit body.

### Audit vs. reality

The audit flagged **four** schema features as removal candidates.
Migration `data/db/migrations/0022_drop_dormant_features.sql` shipped
only one of them; the other three were retained:

| Candidate | Outcome | Reason |
|---|---|---|
| `maintainer_credential` table | **DROPPED in 0022** | WebAuthn passkey schema, never wired to register/assert code. Live DB had zero rows. |
| `ChangeStatus.auto_applied` enum value | **KEPT** | Removing it shrinks SQLAlchemy-emit `target_type VARCHAR(11)` → `VARCHAR(6)`. Live column is `VARCHAR(11)` — a parity-clean removal requires a table-rebuild migration for cosmetic-only gain. |
| `ChangeTarget.trip_report` enum value | **KEPT** | Same VARCHAR-length reason. |
| `EditorStatus.minimal` tier | **KEPT** | Audit was wrong. `admin.php` promotes `pending→minimal` as the first review step; `propose_handler.php` has a `minimal`-specific daily cap (10/day); live DB has 1 editor at this tier. |

The other two rows that appeared in T3.5's plan table
(`rating` / `rating_data` and `ChangeRequestAttachment`) were
"schema-only carry cost" entries the audit listed for completeness,
with KEEP justifications baked in — they were never in flux.

### Where the VARCHAR-length gate lives

`tests/test_db/test_schema_parity.py` compares the SQLAlchemy-emitted
schema (`Base.metadata.create_all` against a fresh `:memory:` DB) with
a fresh `levels init-db` against a tmp-file DB. The check compares
column types as strings, so `VARCHAR(11)` ≠ `VARCHAR(6)` even if the
data fits in both.

For columns backed by Python `Enum`s, SQLAlchemy derives the VARCHAR
length from `max(len(member.name) for member in enum)`. Adding or
removing an enum value can therefore shift the emitted column width,
which trips schema-parity on a live DB that pre-dates the change.

### Dropping a kept enum value later

If a future change makes one of the kept enum values genuinely
unreferenced (e.g. removing the `change_request` flow entirely), the
recipe is:

1. **Schedule a maintenance window.** The migration touches every row
   in `change_request` (or the table that owns the column). It runs
   under `@no_transaction` because `PRAGMA foreign_keys = OFF` has to
   be effective, and SQLite ignores that pragma mid-transaction (see
   § Recovering from a partial `@no_transaction` migration above).

2. **Write a table-rebuild migration**, named per the existing
   `data/db/migrations/NNNN_*.sql` sequence. Pattern (adapted from
   `0012_reach_name_partial_unique.sql`):

   ```sql
   -- @no_transaction
   PRAGMA foreign_keys = OFF;

   CREATE TABLE change_request_new (
       -- ... full column list with the narrower VARCHAR(N) ...
   );

   INSERT INTO change_request_new SELECT * FROM change_request;

   DROP TABLE change_request;
   ALTER TABLE change_request_new RENAME TO change_request;

   -- Re-create every index and trigger that lived on the original.

   PRAGMA foreign_keys = ON;
   ```

3. **Update `src/kayak/db/models.py`** in the same commit: drop the
   enum value. The schema-parity test now passes because the live
   column width matches the ORM-emit width.

4. **Run `levels migrate`** during the maintenance window; verify no
   `_new` tables remain afterwards (see § Recovering from a partial
   `@no_transaction` migration for the cleanup recipe if it dies
   mid-flight).

Don't combine this with other schema changes in the same migration —
table-rebuild migrations are the single load-bearing operation per
file, and combining them complicates recovery.

## Cert renewal

See `DNS.CHANGEOVER.md` for the active cert lifecycle plan
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
   - `orphan-check` — a fetch-active source has no `gauge_source`
     link; the pipeline build still completed but the run is marked
     failed so the operator notices. Triage steps in
     [`docs/migrations.md` § "Reacting to an orphan-check pipeline
     alert"](migrations.md#reacting-to-an-orphan-check-pipeline-alert).

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

## Config

The runtime configuration shared between Python (`kayak.config`) and PHP
(`Config::*` in `php/includes/config.php`) is a single JSON snapshot:

- **Location:** `/etc/kayak/runtime-config.json`, mode `0640 root:www-data`.
  Atomically written by `levels emit-config` (same-dir `.tmp` + `rename(2)`).
- **Schema:** every field on `KayakConfig` (`src/kayak/config.py`) becomes
  a key in the JSON. SecretStr fields land plaintext — the file mode is
  the security boundary, not field masking.
- **Inputs:** `~/.config/kayak/.env` (operator-managed non-secrets) +
  `/etc/kayak/secrets.env` (root:www-data 0600 — Turnstile site key /
  secret). `kayak.config` loads both via `load_dotenv` at import time;
  the secrets.env load is gated on `os.access(R_OK)` so dev shells where
  pat can't read root-owned files silently skip it.

### Inspecting

```bash
# Python view — resolved KayakConfig as a table (no JSON file read).
levels show-config

# PHP view — reads /etc/kayak/runtime-config.json. Refuses HTTP serving.
sudo php /home/pat/kayak/php/show-config.php
```

### Refreshing

`scripts/deploy.sh` re-emits on every deploy: it renders the JSON
**unprivileged** (`levels emit-config --dry-run`, as pat) and pipes it
into the root-owned installer wrapper. The sudoers grant at
`/etc/sudoers.d/kayak-emit-config` allows exactly one command —
`pat ALL=(root) NOPASSWD: /usr/local/sbin/kayak-install-runtime-config`
— never the pat-writable venv `levels` binary (the old
`sudo levels emit-config` grant was a pat→root RCE; review-3 R1.5).
Manual refresh uses the same pipeline:

```bash
/home/pat/.venv/bin/levels emit-config --dry-run \
    | sudo -n /usr/local/sbin/kayak-install-runtime-config
```

PHP picks up the new JSON on the next request (per-request `Config`
singleton; no FPM reload needed).

One-time install of the wrapper + grant (as root; see
`deploy/kayak-install-runtime-config.sh` and
`deploy/sudoers.d/kayak-emit-config` for the full rationale):

```bash
install -m 0755 -o root -g root \
    /home/pat/kayak/deploy/kayak-install-runtime-config.sh \
    /usr/local/sbin/kayak-install-runtime-config
install -m 440 -o root -g root \
    /home/pat/kayak/deploy/sudoers.d/kayak-emit-config \
    /etc/sudoers.d/kayak-emit-config
visudo -cf /etc/sudoers.d/kayak-emit-config   # validate before relying on it
```

### Validation

`levels validate-config --known-env --strict` runs as part of
`scripts/deploy.sh` before `emit-config`. It surfaces:

- Out-of-range or malformed values (pydantic field validators).
- Unknown `KAYAK_*` / `FETCH_*` / `MAIL_*` / `HC_*` / `EDITOR_*` /
  `TURNSTILE_*` / `METADATA_*` / etc. env vars (likely typos) — fatal
  under `--strict`. Intentional non-field names (`KAYAK_DATA`,
  `KAYAK_HOME`, …) are allowlisted in
  `src/kayak/cli/validate_config.py::_EXTRA_KNOWN`; a false positive
  means adding the name there, not dropping `--strict`.

Exit codes: `0` clean, `1` invalid field, `2` runner failure.

### When config is fatal at request time

Phase 4 of T3.3 made `php/includes/config.php` HTTP-500 on a missing
or unparseable JSON; the error gets logged as `[CONFIG-FATAL]` to
`php-fpm`'s journal. If `/login.php` (or anything else PHP) returns 500:

```bash
sudo journalctl -u php8.4-fpm --since '10 min ago' | grep CONFIG-FATAL
```

Common causes: `/etc/kayak/runtime-config.json` missing (re-emit), JSON
outside `open_basedir` (check `deploy/kayak-fpm-pool.conf` — the path
must be in the colon list), or file unreadable by www-data (`ls -la
/etc/kayak/runtime-config.json` should show `-rw-r----- root www-data`).

See § Config drift below for the weekly automated check that catches
on-disk vs. repo skew across all tracked config files.

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

## Rollback (revert code to a previous SHA)

When a deploy ships a regression — broken HTML, a 5xx on PHP pages,
a crashing pipeline run — get back to a known-good code state by
re-running `scripts/deploy.sh` against an earlier commit.

### Decide what to roll back

```bash
# What's at HEAD now (the live SHA).
cd /home/pat/kayak && git rev-parse HEAD

# Recent main commits, with subject lines, for picking the rollback target.
git log --oneline -n 20 main
```

Pick the **most recent SHA before the regression landed.** Tags (when
they exist post-T3.6) are equivalent; until then, SHA is the unit.

### Check whether DB migrations ran since that SHA

`scripts/deploy.sh` runs `levels migrate` on every deploy. If a
migration landed between the rollback target and `HEAD`, the schema
is already at the newer shape and **code rollback alone won't
restore the prior state.** Pre-check:

```bash
# Migrations applied since the rollback target.
git diff --name-only <ROLLBACK_SHA>..HEAD -- data/db/migrations/
```

Schema changes are **forward-only.** The migrations listed below
make `levels migrate` non-reversible by design — once they've run on
prod, "roll back the code" no longer means "roll back the schema."
Re-deploying earlier code over a newer schema is *usually* fine
(SQLite ignores unknown columns; orphan tables sit unread) but is
sometimes load-bearing — see the per-migration notes:

| File | Why it can't be reversed by `levels migrate` |
|---|---|
| `0004_drop_alembic_version.sql`           | drops the legacy Alembic bookkeeping table; harmless to revisit code, but the row history is gone. |
| `0006_drop_pages.sql`                     | drops the `pages` cache table. Earlier code that reads `Page` ORM rows will throw `OperationalError: no such table`. |
| `0011_latest_observation_cascade.sql`     | rebuilds `latest_observation` with cascade FKs (table-recreate). Reverting the rebuild without restoring the previous DDL is non-trivial. |
| `0017_normalize_agency_and_drop_orphan_nwrfc_xml.sql` | drops 11 inactive `fetch_url` rows and rewrites `source.agency`. The deleted URLs aren't recovered by reverting the SQL. |
| `0018_drop_dead_split_sources.sql`        | deletes 19 dead `source` rows. The rows themselves are gone; only a DB restore brings them back. |
| `0022_drop_dormant_features.sql`          | drops `maintainer_credential` (T3.5). Code older than 2026-05-13 doesn't reference it, so the missing table is silent — listed for completeness. |

If your rollback target predates one of the destructive migrations
above AND the older code still reads the dropped object, **restore
the DB from the latest pre-migration backup** before redeploying. See
§Backup + restore above; backups live at
`/home/pat/backups/backup-<UTC-stamp>.db.gz`. The backup
timestamps line up 1:1 with `kayak-backup.timer` runs (Sun 03:15 +
the hourly snapshots from T1.1), so picking the right backup is
matching the backup `<UTC-stamp>` to "just before the bad deploy."

### Execute the rollback

```bash
# Already-clean working tree is enforced by deploy.sh; if you have
# local changes, stash them first.
cd /home/pat/kayak
git fetch origin
git checkout <ROLLBACK_SHA>          # detached HEAD is fine here
git branch -f main HEAD              # move main to the rollback point
git checkout main                    # re-attach

scripts/deploy.sh
```

`deploy.sh` is idempotent: it pulls `--ff-only` (which is now a no-op
since `main` is already at the rollback SHA), re-runs `levels
migrate` (no-op when no new migrations are present), rebuilds static
HTML, and reinstalls pip deps only if `pyproject.toml` changed
direction.

### Verify

```bash
git rev-parse HEAD                                            # should match <ROLLBACK_SHA>
systemctl list-timers --all 'kayak-*' --no-pager | head        # timers still scheduled
curl -fsS -o /dev/null https://levels.mousebrains.com/         # site renders
journalctl -u kayak-pipeline.service --since '15 min ago' | tail
```

Run the recap (see `scripts/recap.py`) after the next `kayak-pipeline`
firing to confirm structured events still flow.

### Push the rollback upstream

After verifying live is healthy at the rollback SHA, push the moved
`main`:

```bash
git push --force-with-lease origin main
```

`--force-with-lease` (not `--force`) refuses to overwrite if anyone
pushed in the meantime — that's the safety belt for the rare two-
operator case. Open a follow-up commit on `main` to undo or fix the
regression rather than leaving `main` permanently behind.

## Bus-factor partner

Single-operator hobby project. If the operator is unreachable for a
period long enough to matter (vacation, hospital, life event), the
site can still serve cached static HTML for weeks — what degrades
first is the freshness SLO (see [docs/slo.md](slo.md) F), and the
recovery flows in this document need a human at the keyboard.

The bus-factor partner is one trusted person who can keep the lights
on for a short window without the operator. **No formal SLA**; the
relationship is "best-effort backup on-call."

### What the partner needs

| Item | Where it lives | Who grants |
|---|---|---|
| Read access to this runbook + `docs/slo.md` + `docs/security/incident-response.md` | Public repo at `github.com/mousebrains/kayak_python` | Already public |
| SSH access to `levels.mousebrains.com` | Hetzner VPS | Operator adds the partner's pubkey to `~/.ssh/authorized_keys` (read-only `pat` access is enough for diagnosis; full deploy access only if the partner is expected to ship fixes) |
| Healthchecks.io view access | Free-tier team feature | Operator invites by email from the project dashboard |
| Better Stack view access | Free-tier team feature | Operator invites by email |
| ntfy.sh topic name | `NTFY_TOPIC` in `~/.config/kayak/.env` | Operator shares the topic out-of-band (1Password, signed message, in-person); rotate after sharing if practical |
| Gmail account credentials *(only if mail-path recovery is expected)* | `pat.kayak@gmail.com` | 1Password share; rotate after revocation |
| `~/.config/kayak/.env` contents | `chmod 600` on the live host | Operator hands over an SSH-copyable snapshot; sensitive (DB url, captcha secrets, `NTFY_TOPIC`) |

The partner does **not** need write access to GitHub or the
`gdrive-crypt:` remote unless they're expected to push hotfixes or
restore from off-site — both are deliberate escalation steps.

### Walkthrough cadence

Once a year (or whenever this runbook changes substantially), the
operator should:

1. Open [`docs/operations.md`](operations.md) and
   [`docs/security/incident-response.md`](security/incident-response.md)
   side-by-side with the partner.
2. Walk through each `§` of this file, confirming the partner can
   find each command and knows what triggers each procedure.
3. Run one practice drill from the partner's machine — typical
   choice is the restore drill (§Backup + restore → Restore from
   local hourly), recovering into a scratch directory rather than
   the live DB.
4. Log the walkthrough date + any gaps surfaced in a short
   addendum at the top of this section (date + initials + "next
   walkthrough due YYYY-MM-DD").

### Escalation path

If something is on fire and the partner is the only available
operator:

1. **Confirm scope.** Open `levels.mousebrains.com`; check the
   Better Stack dashboard; run `systemctl list-timers --all
   'kayak-*' --no-pager` over SSH.
2. **Try the lowest-risk fix first.** A restart (`sudo systemctl
   restart kayak-pipeline.service`) is the lowest-blast-radius
   action; a deploy is next; a restore from backup is the highest
   blast-radius option (do not invoke without operator
   authorization unless the DB is demonstrably corrupt).
3. **Document everything.** Append a short journal entry to
   [`docs/security/incident-response.md`](security/incident-response.md)
   at the bottom (date, action, outcome) so the operator can pick
   up the thread on return.

### Standing list of "things to tell the partner before leaving"

- Where the off-site backup lives (`gdrive-crypt:` rclone remote;
  passphrase in 1Password).
- That ntfy.sh's topic name *is* the credential — don't paste it.
- That `db_push.sh` is operator-only and the partner should NEVER
  run it (live DB overwrite). `db_pull.sh` is safe.
- That the live host runs the `pat` user; there is no separate
  deploy user yet.
- Expected return date + how to reach the operator if absolutely
  required (operator's preferred channel for emergencies).

> *Walkthrough log:*
> *Not yet conducted. First walkthrough scheduled when the partner
> is identified.*

## Quick reference: stop everything before a destructive operation

```bash
# Stop all kayak-* timers (no future triggers) + any running service. The old
# one-liner parsed the ACTIVATES column and stopped *services*, leaving the
# timers to re-fire on schedule; stop the timers (and services) directly:
sudo systemctl stop 'kayak-*.timer' 'kayak-*.service'
```

## When in doubt

- Check `journalctl -u <unit> --since '1 hour ago'`.
- Backups are cheap to make and free to keep — do one before any
  destructive operation.
- The drift detector is the canonical record of what should be on disk
  vs what is. A clean run is a load-bearing baseline.
