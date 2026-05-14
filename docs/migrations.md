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
