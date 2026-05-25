# Migrations — writing and triaging

This doc covers two scenarios that touch `data/db/migrations/`:

- **Writing a migration that deletes source rows** — pre-flight
  checklist so the deletion doesn't leave fetch_url rows pointing at
  nothing, which is how the May 2026 orphan-source incident happened.
- **Reacting to an `orphan-check` pipeline alert** — what to do when
  `kayak-pipeline.service` fails with `RuntimeError: N orphan
  source(s) found`.

For the migration mechanics themselves (where files live, how
`levels migrate` applies them, the `schema_migrations` table), see
the **Schema evolution** bullet block in [`CLAUDE.md`](../CLAUDE.md).

## Writing a migration that deletes sources

The 0018 anti-pattern: deleted 19 source rows, touched no
`fetch_url` row, didn't verify calc inputs still had a live source.
The next `levels fetch` auto-created replacement source rows
(parsers/base.py::`_auto_create_source`) without `gauge_source`
links, and calc gauges that read from the deleted sources stayed
frozen for three days. The fix is a checklist, not a tool — run
through this before adding `DELETE FROM source` to any migration.

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
  **Preferred:** delete the URL from `data/sources.yaml`. The next
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

### 4. Run `levels orphan-check` against the post-migration sandbox

```bash
sqlite3 /tmp/sandbox.db ".restore '/home/pat/DB/kayak.db'"
DATABASE_URL=sqlite:////tmp/sandbox.db /home/pat/.venv/bin/levels migrate
DATABASE_URL=sqlite:////tmp/sandbox.db /home/pat/.venv/bin/levels orphan-check
```

`levels orphan-check` exits 0 with `No orphan sources.` on a clean
post-migration DB. Any non-empty output means step 2 missed
something — go back and decide each surfaced row before committing
the migration.

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

### 2. Decide and write a follow-up migration

For each orphan source, pick one of:

- **Link to a gauge** — the preferred move when the source is still
  emitting useful data. Live data is cheap to keep wired and
  expensive to lose; deactivating a URL only to have auto-create
  re-orphan it next deploy is the mistake we're trying to avoid.
  Example:

  ```sql
  INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (<gauge>, <source>);
  ```

  See migrations 0020 and 0021 for real examples.

- **Deactivate the URL** — when the agency has retired the
  endpoint or the data is genuinely duplicative and unwanted. As
  with step 2 of the writing checklist above, the preferred path is
  to remove the URL from `data/sources.yaml` (the next `levels
  fetch` flips `is_active=0` automatically). Only write an explicit
  `UPDATE fetch_url SET is_active = 0` migration when the URL must
  stay in the YAML for unrelated reasons.

- **Delete the source row** — only when the row's history is not
  worth preserving on another gauge, and only with the 0018-style
  observation re-pointing dance done correctly this time
  (re-point observations to a sibling **and** deactivate / remove
  the fetch_url, then verify with `levels orphan-check`).

After applying the migration, run `levels orphan-check` on prod to
confirm zero rows, and the next pipeline run will exit clean.

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
`data/sources.yaml` but **does not** create any `gauge_source` links
(only migration 0020 ever `INSERT`s into `gauge_source`, and only as a
fix). So `init-db` + `migrate` *alone* yields a DB where every
fetch-backed source is an orphan — `levels orphan-check` would flag
~300 rows.

What closes the gap: the `data/db/*.csv` snapshots (written nightly by
`scripts/export_metadata.py`) are **read back** by
`scripts/import_metadata.py`, which loads `gauge.csv`, `source.csv`,
`gauge_source.csv`, `reach.csv`, and the rest — recreating the live
`gauge_source` links. `reach.geom` is kept out of `reach.csv` (large,
and not regenerable on prod without a DEM/NHD) and lives in
`data/db/reaches.json`; the same script's `--geom-only` pass applies it.

So rebuilding prod from scratch — e.g. after catastrophic corruption
with no usable backup — **is** possible purely from what's checked in:

```bash
levels init-db --no-seed                       # empty schema + stamped migrations
python scripts/import_metadata.py              # CSVs → gauges/sources/reaches/links
python scripts/import_metadata.py --geom-only  # reaches.json → reach.geom
levels pipeline                                # fetch live data + render
```

`--no-seed` skips the `sources.yaml` state/source seed so the canonical
CSV rows import without duplicate-by-name sources. The import loads with
FK enforcement off (the live DB carries intentional orphan rows) and
reports `integrity_check` + `foreign_key_check` afterward. This is the
same sequence the quick-start (`README.md`, `deploy/SETUP.md` §4) uses
for a fresh install; `--geom-only` is also how a dev re-trace's geometry
reaches prod (see `deploy/SETUP.md` §4 and `scripts/deploy.sh`).

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
