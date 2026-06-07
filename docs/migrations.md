# Migrations and metadata edits — writing and triaging

Schema and metadata changes now travel by **different paths**:

- **Schema** (table shape: ALTER / DROP / CHECK / index) → a
  `src/kayak/data/db/migrations/NNNN_*.sql` applied by `levels migrate`.
  **Schema-only** since the metadata-single-source redesign — a new
  migration may not INSERT/UPDATE/DELETE a metadata table (guard:
  `tests/test_scripts/test_migrations_schema_only.py`).
- **Metadata** (source / gauge / reach / junction rows) → a reviewed
  CSV diff in the **`kayak_data`** repo (cloned at `DATASET_DIR`),
  applied by `levels sync-metadata` (deploy.sh step 3.1), matched by
  stable id. See
  [`PLAN_add_gauges_reaches.md`](PLAN_add_gauges_reaches.md) for the
  add / update / remove / split runbooks.
- **Dataset contract** (`dataset.yaml` at the dataset root) → declares the
  `contract_version` the dataset was authored against (plus `dataset_id`,
  `name`, `status`, `license`, `engine_test_ref`). The engine reads a supported
  contract range (`kayak.dataset.contract`); a dataset with no `dataset.yaml`
  is **contract 0** and is rejected by `levels validate-dataset`. Contract 1
  also requires a `retired_ids.yaml` sidecar at the root (`{}` when nothing is
  retired) recording purged stable ids per id-bearing table, so a deleted row's
  id is never reused and the id counter stays above it. To clear contract 0 on
  an existing dataset, add a `dataset.yaml` with `contract_version: 1` and the
  required fields, plus a `retired_ids.yaml` (`{}`). A future contract bump
  ships an `upgrade-dataset` transform or a manual-migration note here
  (dataset-separation S6).

This doc covers the hazards that outlive that mechanism change:

- **Removing a source safely** — the calc-input / fetch_url pre-flight
  so a delete doesn't leave a fetch_url pointing at nothing or a calc
  dangling (the May 2026 orphan incident). It's a CSV delete via
  `sync-metadata --allow-deletes` now, not a `DELETE FROM source`
  migration — the mechanism changed, the hazards didn't.
- **Reacting to an `orphan-check` pipeline alert** — what to do when
  `kayak-pipeline.service` fails with `RuntimeError: N orphan
  source(s) found`.

For the schema-migration mechanics (where files live, how
`levels migrate` applies them, the `schema_migrations` table), see the
**evolution** bullet block in [`CLAUDE.md`](../CLAUDE.md).

## Removing a source safely (CSV + sync)

The 0018 anti-pattern: deleted 19 source rows, touched no
`fetch_url` row, didn't verify calc inputs still had a live source.
The next `levels fetch` auto-created replacement source rows
(parsers/base.py::`_auto_create_source`) without `gauge_source`
links, and calc gauges that read from the deleted sources stayed
frozen for three days. The fix is a checklist, not a tool — run
through this before deleting a source's row from `kayak_data`'s
`source.csv` (plus its `gauge_source.csv` link, and `fetch_url.csv` /
the code repo's `src/kayak/data/sources.yaml` if it's a fetch source).

### 1. List every fetch_url referenced by the deleted sources

```sql
SELECT id, url, is_active
FROM fetch_url
WHERE id IN (
    SELECT DISTINCT fetch_url_id FROM source WHERE id IN (...)
);
```

If `fetch_url_id` is NULL for any deleted source, that source is a
calc-only row — skip ahead to step 3.

### 2. Decide each fetch_url's fate

For each URL the listing returns:

- **Another live source still consuming it?** Check with
  `SELECT id, name FROM source WHERE fetch_url_id = X AND id NOT IN
  (<deleted ids>)`. If non-empty, leave the fetch_url alone — the
  surviving source still needs it.

- **No other consumer, and the URL is genuinely retired?**
  **Preferred:** delete the URL from `src/kayak/data/sources.yaml`. The next
  `levels fetch` runs `sync_sources`
  (`src/kayak/cli/init_db.py::sync_sources`, called at
  `src/kayak/cli/fetch.py:150`) which flips `is_active=0` on any
  fetch_url whose URL is no longer in the YAML; the fetch loop
  already skips URLs not in YAML — `is_active` is an audit marker,
  not the gate. The migration only needs to touch the source-row
  table.

- **No other consumer, but the URL must stay in YAML?** (Edge case:
  another station in the URL's `stations:` block is still wired up,
  e.g., multi-station feeds where one station went away but others
  remain.) Add an explicit `UPDATE fetch_url SET is_active = 0
  WHERE id = …` to the migration — the 0019 pattern.

### 3. Verify calc-gauge inputs

If any of the deleted sources fed a gauge that's a calc input (i.e.
another source's `calc_expression.time_expression` references that
gauge by name), confirm that another live source still produces the
calc's input data_type for that gauge:

```sql
-- Find calc sources that read from the affected gauge by name
SELECT s.id, s.name, ce.expression, ce.time_expression
FROM source s
JOIN calc_expression ce ON ce.id = s.calc_expression_id
WHERE ce.time_expression LIKE '%::<gauge_name>::<data_type>';
```

Then check the gauge's remaining linked sources:

```sql
SELECT s.id, s.name, s.fetch_url_id
FROM source s
JOIN gauge_source gs ON gs.source_id = s.id
WHERE gs.gauge_id = <gauge_id> AND s.id NOT IN (<deleted ids>);
```

If the answer is "no live source produces the input data_type
anymore," the calc will silently freeze on whatever it last read.
Either pull the calc source from the migration too, or rewire the
calc to a different gauge — but do **not** ship a deletion that
leaves a calc dangling. The May 2026 incident root-caused to a
missing check here.

### 4. Apply to a sandbox and run `levels orphan-check`

```bash
sqlite3 /tmp/sandbox.db ".restore '/home/pat/DB/kayak.db'"
# Review the irreversible per-source observation-drop counts first:
DATABASE_URL=sqlite:////tmp/sandbox.db /home/pat/.venv/bin/levels sync-metadata --dry-run
DATABASE_URL=sqlite:////tmp/sandbox.db /home/pat/.venv/bin/levels sync-metadata --allow-deletes
DATABASE_URL=sqlite:////tmp/sandbox.db /home/pat/.venv/bin/levels orphan-check
```

`levels orphan-check` exits 0 with `No orphan sources.` on a clean
post-sync DB. Any non-empty output means step 2 missed something — go
back and decide each surfaced row before committing the CSV diff.

Cleanup: `rm /tmp/sandbox.db /tmp/sandbox.db-wal /tmp/sandbox.db-shm`.

## Reacting to an "orphan-check" pipeline alert

The pipeline ends with an `orphan-check` step
(`src/kayak/cli/pipeline.py::_orphan_check`); if it finds any
fetch-active source with no `gauge_source` link, it raises
`RuntimeError`, the pipeline records the failure and exits non-zero,
and systemd's `OnFailure=kayak-notify-failure@%n.service` fires the
existing email + ntfy chain. You'll see:

> Subject: Kayak: kayak-pipeline.service failed

### 1. Enumerate the orphans

```bash
/home/pat/.venv/bin/levels orphan-check
# or, post-mortem from the alert email's journal pointer:
sudo journalctl -u kayak-pipeline --since '1 hour ago' --no-pager | grep -A1 orphan-check
```

`--json` if you want to script over the result.

### 2. Decide the fix and edit the CSV

For each orphan source, pick one of:

- **Link to a gauge** — the preferred move when the source is still
  emitting useful data. Live data is cheap to keep wired and
  expensive to lose; deactivating a URL only to have auto-create
  re-orphan it next deploy is the mistake we're trying to avoid. Add
  the join row to `kayak_data`'s `gauge_source.csv`:

  ```
  gauge_id,source_id
  <gauge>,<source>
  ```

  (Migrations 0020/0021 are the historical `INSERT OR IGNORE INTO
  gauge_source` equivalents.) `levels sync-metadata` inserts it by id.

- **Deactivate the URL** — when the agency has retired the endpoint
  or the data is genuinely duplicative. Preferred: remove the URL from
  `src/kayak/data/sources.yaml` (the next `levels fetch` flips `is_active=0`
  automatically). Only set `is_active=0` in `kayak_data`'s
  `fetch_url.csv` directly when the URL must stay in the YAML for
  unrelated reasons.

- **Delete the source row** — only when the row's history isn't worth
  preserving on another gauge. Remove it from `source.csv` (plus its
  `gauge_source.csv` link) and run the *Removing a source safely*
  checklist above; `sync-metadata --allow-deletes` drops it (printing
  the observation-drop count) and cascades the `gauge_source` /
  `latest_*` rows. Re-point observations to a sibling first if the
  history matters.

After the CSV diff merges and deploys (`sync-metadata` at step 3.1),
run `levels orphan-check` on prod to confirm zero rows; the next
pipeline run exits clean.

## Future work — known graph-integrity gaps

These were called out as "Out of scope" by
`docs/done/PLAN_orphan_sources.md` so the orphan-check work stayed
focused. Each one is a real bug surface that a follow-on plan
should pick up.

### Adjacent graph-health checks

`find_orphan_sources` (in `src/kayak/db/sources.py`) detects one
specific invariant violation: a fetch-backed `source` row with no
`gauge_source` link.

On 2026-05-15, three of the originally-listed sibling invariants
were dropped as intentional design states rather than violations:

- **gauges with no `gauge_source` link** — data providers come and
  go but the gauge's historical observations are worth preserving
  even when no live source remains. The two affected gauges on the
  live DB (ids 87, 89) are kept on purpose.
- **reaches with no `gauge_id`** — not every reach has a monitored
  gauge; ~43 reaches on the live DB are in this state by design
  (many WA/ID/CA runs are tracked without one).
- **active `fetch_url` with no source** — zero live violations on
  prod and the check would almost never fire.

The one sibling invariant still worth a follow-on plan is:

| invariant | violation symptom |
|-----------|-------------------|
| Every `calc_expression.time_expression` resolves to a live gauge + data_type | The May 2026 incident was downstream of this; the orphan-check catches the upstream symptom but not the calc-side staleness directly. Trickiest of the four to implement — requires evaluating the time-expression's gauge-name resolution. |

### Rebuilding a DB from the checked-in snapshot (recovery runbook)

> This was previously listed here as a future-work gap — "rebuilding
> prod from scratch is not possible … no code path reads the CSVs back."
> That gap is **closed**: `scripts/import_metadata.py` reads the CSV
> snapshots back into the DB (added in the #18/#22 deploy/onboarding
> work). This section is now the recovery runbook, not an open problem.

`levels init-db` seeds `state`, `source`, and `fetch_url` from
`src/kayak/data/sources.yaml` but **does not** create any `gauge_source` links
(only migration 0020 ever `INSERT`s into `gauge_source`, and only as a
fix). So `init-db` + `migrate` *alone* yields a DB where every
fetch-backed source is an orphan — `levels orphan-check` would flag
~300 rows.

What closes the gap: the `kayak_data` CSV snapshots (written nightly by
`scripts/export_metadata.py` to `DATASET_DIR`) are **read back** by
`scripts/import_metadata.py`, which loads `gauge.csv`, `source.csv`,
`gauge_source.csv`, `reach.csv`, and the rest — recreating the live
`gauge_source` links. `reach.geom` and `reach.gradient_profile` are kept
out of `reach.csv` (large, not regenerable on prod without a DEM/NHD) and
live in `kayak_data`'s `reaches.json` + `reaches-gradient.json`; a full
`import_metadata.py` run applies both after the CSVs in the same invocation
(the `--geom-only` / `--gradient-only` flags re-apply *only* one to a live
DB — the dev re-trace path, see `deploy/SETUP.md` §4 and `scripts/deploy.sh`;
a from-scratch rebuild doesn't need them).

`huc_name.csv` carries only the HUC6 + HUC8 names the site resolves; the
HUC2/4/10/12 levels (≈97% of WBD's 17k rows) were trimmed in migration
0061 and `levels assign-huc` no longer writes them (review-3 R6.2), so a
rebuild restores the 403 names the UI needs, not the full WBD lookup.

So rebuilding prod from scratch — e.g. after catastrophic corruption
with no usable backup — **is** possible purely from what's checked in:

```bash
levels init-db --no-seed           # empty schema + stamped migrations
python scripts/import_metadata.py  # CSVs (incl. gauge_source) + reaches.json geom
levels pipeline                    # fetch live data + render
```

`--no-seed` is required, not just advisable: a plain `levels init-db`
seeds `state` / `source` / `fetch_url` from `sources.yaml` with fresh
ids, which then collide with the canonical-id CSV rows on import — a
duplicate `source` (its name isn't unique), or, since the import upserts
on the primary key, an *aborting* `UNIQUE` conflict on `state.name` /
`fetch_url.url`. `--no-seed` gives empty tables so the CSV ids load
cleanly. The import loads with FK enforcement off (the live DB carries
intentional orphan rows) and reports `integrity_check` +
`foreign_key_check` afterward. This is the same sequence the quick-start
(`README.md`, `deploy/SETUP.md` §4) uses for a fresh install.

### Smaller follow-ups (one-line each)

- **Rename `_auto_create_source`** to express the danger surface
  more clearly (e.g., `_auto_create_orphan_source` when
  `source_map` is empty). Rejected during the orphan-source plan
  as not worth the churn; revisit if the function gains more
  conditional behavior.
- **Schema-level FK / CHECK constraint** that fetch-backed sources
  must have a `gauge_source` link. SQLite triggers don't span tables
  cleanly and would block legitimate intermediate migration states.
  Phase 2b's post-pipeline check covers the same invariant at
  query-time, but a constraint would catch violations earlier.
- **Orphan-source deletion path.** Currently the only documented
  recovery for an orphan is "link it to a gauge" or "deactivate
  the URL." Deleting the source row itself requires the
  0018-style observation re-pointing dance, which has no helper
  yet. A `levels delete-orphan-source --id N --reroute-to M`
  subcommand would close that gap.
