# Pulling and Pushing the Live Database

Two workflows for copying the kayak database between the live server
(`levels.mousebrains.com`) and a developer workstation, without losing
observations that accumulate on live while you're editing locally.

- **Pull:** `scripts/db_pull.sh` — snapshot live `~/DB/kayak.db` into local `../DB/kayak.db`.
- **Push:** `scripts/db_push.sh` — ship local edits back, merging in live observations.

Both scripts use compressed snapshots staged in `~/kayak/backups/` on the remote.

## Prerequisites

- Passwordless SSH to `pat@levels.mousebrains.com` (tested with `ssh pat@levels.mousebrains.com true`).
- `sqlite3`, `rsync`, `gzip` on both hosts.
- `sudo -n systemctl {start,stop} kayak-<unit>` usable non-interactively by `pat` on the server. See `deploy/sudoers.d/kayak-pipeline` for a sample drop-in.
- Enough free space in `~/kayak/backups/` on the remote for a few compressed DB snapshots (typically 50–200 MB each).

## Layout

Scripts assume the repo lives at `<somewhere>/kayak/` and the local DB sits next to it at `<somewhere>/DB/kayak.db` (i.e. `../DB/kayak.db` from the repo root). This matches `systemd/kayak-backup-weekly.sh` on the server.

Override via env vars if needed:
```
REMOTE_HOST=pat@levels.mousebrains.com
REMOTE_DB=/home/pat/DB/kayak.db
REMOTE_BACKUP_DIR=/home/pat/kayak/backups
```

## Pull

```bash
cd ~/tpw/kayak
./scripts/db_pull.sh        # prompts before overwriting an existing local DB
./scripts/db_pull.sh -f     # skip the prompt
```

What it does:

1. On the remote: `sqlite3 ~/DB/kayak.db ".backup ~/kayak/backups/kayak-<UTC>.db"` — consistent point-in-time copy, safe under concurrent writes from the pipeline.
2. `gzip -9` the snapshot.
3. `rsync` the `.gz` to `../DB/`.
4. `gunzip` to `../DB/kayak.db`, remove any stale `-wal`/`-shm` files.
5. Record the snapshot name in `../DB/.pulled_snapshot` (handshake with `db_push.sh`).
6. Print row counts and the newest `observed_at` in the observation table.

After the pull you can edit `../DB/kayak.db` with `sqlite3`, via the `levels` CLI, or through the PHP editor if you have one pointed at it.

## Push

```bash
cd ~/tpw/kayak
./scripts/db_push.sh        # prompts for confirmation
./scripts/db_push.sh -f     # skip the prompt
```

What it does:

1. Local: `PRAGMA wal_checkpoint(TRUNCATE)`, then `sqlite3 .backup` → `kayak-from-local-<UTC>.db`, `gzip -9`.
2. `rsync` the `.gz` to `~/kayak/backups/` on the remote.
3. On the remote, within a single SSH session:
   - Stop `kayak-pipeline.{timer,service}`, `kayak-decimate.{timer,service}`, `kayak-backup-weekly.{timer,service}`, `kayak-backup-hourly.{timer,service}`.
   - `PRAGMA wal_checkpoint(TRUNCATE)` on live, then `.backup` it to `/tmp/kayak-live-final-<ts>.db` — this captures every observation the pipeline wrote while we were editing.
   - `gunzip` our uploaded snapshot to `/tmp/kayak-new-<ts>.db`.
   - Merge via `sqlite3`:
     - `INSERT OR IGNORE` every observation from the live-final DB into the new DB, filtered by a `JOIN` on `source` (skips rows whose source was deleted locally).
     - Replace `latest_observation` / `latest_gauge_observation` from live-final.
     - Clear `pages` (the next `build` regenerates it).
   - `PRAGMA integrity_check` on the merged DB; abort and restart timers if it's not `ok`.
   - Archive the outgoing live DB to `~/kayak/backups/kayak-replaced-<ts>.db.gz`.
   - Atomically `mv /tmp/kayak-new-<ts>.db ~/DB/kayak.db`.
   - Restart the timers.

Result: your metadata edits are live, and no observations were lost.

## What gets preserved vs. overwritten

| Table(s) | On push |
|---|---|
| `observation` | **Union** — your local rows + every live row via `INSERT OR IGNORE`. |
| `latest_observation`, `latest_gauge_observation` | **Replaced from live's final state.** The next pipeline tick will recompute them anyway. |
| `pages` | **Cleared.** `levels build` regenerates it on the next run. |
| `source`, `gauge`, `gauge_source`, `fetch_url`, `calc_expression` | **Local wins.** |
| `reach`, `reach_state`, `reach_class`, `class_description`, `guidebook`, `reach_guidebook` | **Local wins.** |
| `rating`, `rating_data` | **Local wins.** |
| `state` | **Local wins.** |

## Rollback

Every push archives the pre-push live DB:

```
~/kayak/backups/kayak-replaced-<UTC>.db.gz
```

To revert, on the remote:

```bash
sudo systemctl stop kayak-pipeline.timer kayak-decimate.timer kayak-backup-weekly.timer kayak-backup-hourly.timer
sudo systemctl stop kayak-pipeline.service
mv ~/DB/kayak.db ~/DB/kayak.db.bad
gunzip -c ~/kayak/backups/kayak-replaced-<UTC>.db.gz > ~/DB/kayak.db
chmod 664 ~/DB/kayak.db
rm -f ~/DB/kayak.db-wal ~/DB/kayak.db-shm
sudo systemctl start kayak-pipeline.timer kayak-decimate.timer kayak-backup-weekly.timer kayak-backup-hourly.timer
```

## Snapshot retention

`db_pull.sh` and `db_push.sh` prune their own snapshots so `~/kayak/backups/`
(and the local `../DB/`) don't grow unbounded:

- **Pull snapshots** (`kayak-<UTC>.db.gz`): newest `KEEP_PULL_SNAPSHOTS` kept
  (default 3) on both the remote and locally; older pruned each pull.
- **Pre-push archives** (`kayak-replaced-<UTC>.db.gz`): newest
  `KEEP_PUSH_ARCHIVES` kept (default 5) on the remote; older pruned each push.
- **Staging** (`kayak-from-local-<UTC>.db.gz`): deleted after the swap consumes
  it (the local copy is already removed at the end of the push).

These are transient sync artifacts — the real backup rotation is the systemd
`kayak-backup-hourly` / `kayak-backup-weekly` units (24 / 4 copies). The prune
globs are anchored (`kayak-[0-9]*T…`, `kayak-replaced-[0-9]*T…`) so the three
artifact types never delete one another.

## Caveats

- **Do not edit `observation` locally.** Live-side rows override nothing (`INSERT OR IGNORE` skips on primary-key conflict), so any local changes silently lose to whatever live already has for the same `(source_id, observed_at, data_type)`. Use `calc-rating` or other pipeline commands on the server if you need to fix observations.
- **Deleting a source locally drops that source's observations from live on push.** The JOIN on `main.source` filters them out to avoid FK violations. If that's not what you want, don't delete sources — deactivate them instead.
- **Metadata changes on live during your editing window are lost on push.** If someone uses `php/edit.php` to change a reach while you're editing locally, the push will clobber their edit. Coordinate out-of-band.
- **The `.pulled_snapshot` file is the handshake.** `db_push.sh` refuses to run without it. If you hand-built the local DB via some other path, fake the handshake with `touch ../DB/.pulled_snapshot` — but understand you're bypassing the "did this come from a pull?" safety check.
- **Downtime during push:** the pipeline is stopped from "start of merge" to "end of merge" — usually under a minute. If you can't afford even that, run the push when you're OK with a skipped pipeline tick.
