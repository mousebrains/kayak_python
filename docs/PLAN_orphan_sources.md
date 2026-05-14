# Plan — Stop fetch silently feeding orphan sources

**Status:** Drafted 2026-05-14 against `main` at `c5e073f` (migration
0020 just landed; gauges 161 + 174 are live again). Not yet executed.
The plan targets the systemic bug behind today's incident: auto-create
mints `source` rows without `gauge_source` links, so when a deletion
migration removes a source but leaves its `fetch_url` active, the next
pipeline run silently rebuilds an orphan that fetches forever into a
dead end.

> **Iter log:**
> - draft (2026-05-14): one pass.
> - iter 1 (2026-05-14): 4 findings from user discussion.
>   - Phase 2a: dropped sentinel file at
>     `~/.cache/kayak/orphan_auto_creates.txt`; rely on Phase 2b's
>     same-run check to surface the orphan instead.
>   - Phase 2b: switched from short-circuit `PipelineInvariantError`
>     to soft-fail (append to existing `failures` list, build still
>     runs, existing `OnFailure=kayak-notify-failure@%n.service`
>     emails + ntfys). No new email plumbing.
>   - Phase 3: confirmed `docs/migrations.md` as a new file (not a
>     section in `docs/operations.md`); audience is migration-authors
>     pre-flight, not operators triaging an alert.
>   - Current state + Phase 0: original draft missed that source 298
>     emits only `gauge` (no flow conflict), and that the DSG
>     endpoints dropped `gauge`+`temperature` ~2026-05-11 so the
>     orphan STG/WTM sources are the **sole live source** for those
>     data_types on gauges 150 + 184. Phase 0 unsticks 6 stale
>     data_type rows, not just N orphan links. Table rewritten with
>     per-source data_types and a freshness-comparison sub-table.
>
> Dates absolute. Citations `file:line` against current `main`.

## Context — what happened on 2026-05-11/14

Migration 0018 (2026-05-11 22:34 UTC) deleted 19 "dead split" source
rows. Two of those (gauge 161 Applegate_Lake's APLO3+14361900, gauge
174 Fall_Creek_Inflow's FALO3) fed `inflow` into `inflow_to_flow` calc
sources. The migration re-pointed their observation history onto the
calcs (200 and 198) but left the underlying `fetch_url` rows
(`is_active=1`) untouched. The next `levels fetch` run hit those URLs,
saw no source for the station name, and
`src/kayak/parsers/base.py:165::_auto_create_source` minted fresh
rows (299 APLO3, 300 FALO3) — `Source(name=..., agency=..., fetch_url_id=...)`
only, no `gauge_source` link. Three days of fresh inflow flowed into
those rows; `update_latest_gauge` (`src/kayak/db/cache.py:160`) never
looked at them because it filters on `gauge_source.source_id`. Reach 300
(MF Applegate) and the Fall Creek reaches displayed Tuesday's frozen
values until migration 0020 landed today.

The same pattern affects five more sources still live on the host (see
**Current state** below): each one fetches healthily, none flow into a
gauge.

## Why this matters / why it'll recur

- **Detection lag is unbounded.** The site shows a stale value with no
  visible error indicator; nothing pages, nothing logs ERROR. Three
  days of staleness made it as far as the daily review queue only
  because the user happened to look at MF Applegate. A river that
  nobody checks could be stale for a season.
- **Migration discipline is informal.** 0018 vs 0019 demonstrate the
  gap: 0019 explicitly toggles `is_active=0` on the URL it replaces;
  0018 deletes 19 sources and touches no `fetch_url` row. There is no
  template, no CI check, and no runbook entry that says "deleting a
  source means deactivating or rewiring its URL."
- **`_auto_create_source` is by design generous.** Multi-station feeds
  (USGS basin queries, the wa.gov station dirs) legitimately need to
  mint source rows on first observation. We can't disable auto-create
  without forcing every parser to pre-declare every station.
- **The fetch pipeline has no end-to-end invariant check.** Each step
  is correct in isolation; nothing asserts that the cross-table graph
  is healthy after the run (every active fetch_url is consumed by ≥1
  gauge).

Goal: make the "active URL → orphan source" loop loud and uncrossable,
without breaking legitimate multi-station auto-create. Failure mode
shifts from "silent stale gauge for days" to "fetch run prints a clear
error pointing at the unlinked source and the URL feeding it" on the
first orphan run.

## Current state (verified on `levels`, post-0020)

5 fetch-backed `source` rows have no `gauge_source` link, with the
data_types each one actually emits:

| id  | name   | parser    | url            | data_types emitted | latest_obs (UTC) |
|-----|--------|-----------|----------------|--------------------|------------------|
| 294 | 29C100 | wa.gov    | …29C100_STG_FM | gauge              | 2026-05-14 16:15 |
| 295 | 29C100 | wa.gov    | …29C100_WTM_FM | temperature        | 2026-05-14 16:15 |
| 296 | 28B080 | wa.gov    | …28B080_STG_FM | gauge              | 2026-05-14 16:15 |
| 297 | 28B080 | wa.gov    | …28B080_WTM_FM | temperature        | 2026-05-14 16:15 |
| 298 | WASW1  | nwps      | …WASW1/stageflow/observed | gauge   | 2026-05-14 17:15 |

These aren't just "duplicate data on a gauge that's already fine." The
DSG endpoints feeding gauges 150 and 184 **stopped emitting `gauge` and
`temperature` around 2026-05-11**; only `flow` is still landing on the
linked sibling source. The orphan STG/WTM endpoints are the **only
fresh source for two data_types on each gauge**.

Confirmation from the live cache:

| gauge | data_type   | latest_gauge_observation | freshest live source (data wasted while orphan) |
|-------|-------------|--------------------------|-------------------------------------------------|
| 150   | flow        | 2026-05-14 16:15         | source 182 DSG (linked)                          |
| 150   | gauge       | **2026-05-11 20:15**     | source 294 STG — 3-day fresher                   |
| 150   | temperature | **2026-05-11 20:15**     | source 295 WTM — 3-day fresher                   |
| 184   | flow        | 2026-05-14 16:15         | source 220 DSG (linked)                          |
| 184   | gauge       | **2026-05-11 21:15**     | source 296 STG (5.05ft) or 298 NWPS (5.02ft) — both 3-day fresher |
| 184   | temperature | **2026-05-11 20:15**     | source 297 WTM — 3-day fresher                   |

Phase 0 unsticks **6 stale data_type rows** across 2 gauges, not just
the orphan-link count. Reaches downstream of gauges 150 and 184 will
show fresh gauge-height + water-temperature for the first time since
2026-05-11.

Code surfaces:
- `src/kayak/parsers/base.py:165` — `_auto_create_source` (the
  silent-orphan factory; warns but doesn't block).
- `src/kayak/parsers/base.py:143-153` — caller in `dump_to_db`.
- `src/kayak/db/cache.py:149-220` — `update_latest_gauge` filtering on
  `gauge_source`.
- `src/kayak/db/cache.py:230+` — `update_all_latest_gauges` (bulk
  refresh; same filter).
- `src/kayak/cli/init_db.py:49+` — `sync_sources` (pre-creates source
  rows from `data/sources.yaml` `stations:` blocks; this is the
  intended non-auto path).
- `data/db/migrations/0018_drop_dead_split_sources.sql` — the
  hygiene-gap exemplar.
- `data/db/migrations/0019_wire_eugo3_nwrfc_textplot.sql:29-30` — the
  hygiene-good exemplar (`UPDATE fetch_url SET is_active = 0 WHERE url
  = …`).

## Phase 0 — Backfill the 5 known orphans (1 migration, ~10 min)

Decide each row's fate, then ship as migration `0021_resolve_orphan_sources.sql`.
Defaults below are reasonable but **the user owns the call per row**:

| id  | suggestion                                  | rationale |
|-----|---------------------------------------------|-----------|
| 294 | link to gauge 150 (29C100) | sole live source of `gauge` for this gauge since 2026-05-11; not a redundancy add — a restoration |
| 295 | link to gauge 150           | sole live source of `temperature` since 2026-05-11 |
| 296 | link to gauge 184 (28B080) | sole live source of `gauge` since 2026-05-11 |
| 297 | link to gauge 184           | sole live source of `temperature` since 2026-05-11 |
| 298 | link to gauge 184 as secondary `gauge` feed | WASW1 NWPS-only feed kept as a sibling to 296's wa.gov STG; insert-into philosophy. Both emit `gauge` at ~15-min cadence; `update_latest_gauge` picks the row with the later `observed_at`, so the gauge value can flicker between 5.02 ft (NWPS) and 5.05 ft (wa.gov). Cosmetic in this case; flagged for awareness |

Migration shape:

```sql
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (150, 294);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (150, 295);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 296);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 297);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 298);
```

Verification: `levels migrate`, then run the orphan query (Phase 1) and
confirm it returns zero rows. Re-run `update-gauge-cache` and confirm:
- gauges 150 + 184 `gauge` and `temperature` rows are now 2026-05-14
  fresh (not still pegged at 2026-05-11);
- gauge 184 `gauge` value sits at whichever of 5.02 / 5.05 ft has the
  latest `observed_at` (sanity check that both feeds are participating);
- no regression on `flow` (DSG sources stay authoritative).

## Phase 1 — Detection: `levels orphan-check` (1 CLI, ~30 min)

A new top-level subcommand that prints (and optionally exits non-zero
on) any active source whose data path doesn't reach a gauge. Use the
exact query that built the table above:

```sql
SELECT s.id, s.name, s.agency, fu.url, fu.is_active,
       lo.observed_at AS latest_obs
FROM source s
LEFT JOIN gauge_source gs ON gs.source_id = s.id
LEFT JOIN fetch_url fu ON fu.id = s.fetch_url_id
LEFT JOIN latest_observation lo ON lo.source_id = s.id
WHERE gs.source_id IS NULL
  AND s.fetch_url_id IS NOT NULL
  AND (fu.is_active = 1 OR lo.observed_at > datetime('now', '-7 days'))
GROUP BY s.id;
```

The `is_active = 1 OR latest_obs > 7d` clause captures both "fetch is
still firing" and "fetch was deactivated very recently and a stale
orphan is hanging around."

Wiring:
- `src/kayak/cli/orphan_check.py` — `addArgs` + `orphan_check(args)`,
  same shape as `decimate.py`. Exposes a callable
  `find_orphan_sources(session)` returning the same rows, so Phase
  2b's in-process pipeline step can import it instead of duplicating
  the query.
- `src/kayak/cli/main.py` — import + register.
- Flag `--exit-nonzero-if-found` for ad-hoc CI / scripting use.
- Output: human table by default; `--json` for tooling consumers.

Verification:
- After Phase 0 lands, `levels orphan-check` returns zero rows on
  prod.
- Synthetic test: in pytest, create a Source with `fetch_url_id` set
  and no `gauge_source` row, assert `find_orphan_sources(session)`
  returns it and the CLI exits non-zero with `--exit-nonzero-if-found`.
- Integration: copy the live DB to a sandbox, manually `INSERT INTO
  observation` for a fake orphan, run the CLI, see the row.

## Phase 2 — Prevention: loud auto-create + post-build report (~45 min)

Two layered fixes, both small, both independent.

**2a. `_auto_create_source` escalates when its URL has no other live
source.** Before minting a new row, check whether any *other* live
source exists for this `fetch_url_id`:
1. Yes — multi-station feed (USGS basin queries, the wa.gov station
   dirs); auto-create is legitimate. Keep current `WARNING` log.
2. No — the URL is "orphaned of sources." The dangerous case. Mint
   the row so the parser doesn't crash mid-batch, but log at `ERROR`
   with both the new source.id and the fetch_url to help the
   operator decide between link-to-gauge and deactivate-url.

That's it for 2a: no sentinel file, no second persistence path. The
loud log gets caught by Phase 2b's end-of-pipeline check on the same
run, so a one-shot log line is enough — no need for a "did this fire
last run?" trail.

**2b. End-of-pipeline orphan-check, soft fail.** Append a final step
to the pipeline DAG (after `build`):

```
("orphan-check", _orphan_check)
```

`_orphan_check` runs Phase 1's query in-process. If non-empty:
- Log ERROR with the orphan table (one row per orphan source).
- Append `("orphan-check", "N orphan sources found")` to the
  pipeline's existing `failures: list[tuple[str, str]]`.

The existing fail-fast logic in `src/kayak/cli/pipeline.py` already
turns a non-empty failures list into a non-zero exit. The systemd
unit's `OnFailure=kayak-notify-failure@%n.service`
(`systemd/kayak-pipeline.service:4`) is wired to the existing
`kayak-notify-failure@.service` which emails `pat.kayak@gmail.com`
and pushes to ntfy.sh — both fire automatically when the pipeline
exits non-zero.

**Important:** soft fail, not short-circuit. The build still runs to
completion (Phase 1's end-of-pipeline placement, not mid-pipeline) so
the public site stays fresh on the data we *do* have. Only the
notification fires.

The notification email subject reads "Kayak: kayak-pipeline.service
failed"; details (the orphan list) live in `journalctl -u
kayak-pipeline`. That's the same operator workflow as any other
pipeline failure, so no new runbook is needed beyond Phase 3's
migrations doc.

Cost: one extra query per pipeline run (cheap; current orphan query
returns in <50ms on the live DB).

Test the failure path: `tests/test_pipeline.py` — synthesize the
"new orphan in this run" state, assert the pipeline records the
orphan-check failure in the failures list and exits non-zero, **but
also asserts the build artifact was written** (proving soft-fail
behavior).

## Phase 3 — `docs/migrations.md` (docs only, ~15 min)

New file at `docs/migrations.md` — separate from `docs/operations.md`
because the audience is different: a future migration-author needs a
pre-flight checklist, not a runbook for an operator triaging an alert.

Outline:

### Writing a migration that deletes sources
1. List every `fetch_url` referenced by the deleted sources:
   `SELECT id, url FROM fetch_url WHERE id IN (SELECT DISTINCT
   fetch_url_id FROM source WHERE id IN (...));`
2. For each fetch_url, decide:
   - Another live source still consuming it? Leave `is_active=1`.
   - No other consumer? `UPDATE fetch_url SET is_active = 0 WHERE id
     = …` in the same migration (the 0019 pattern; the 0018
     anti-pattern is what caused the May 2026 orphan incident).
3. If the deleted source fed a calc gauge (i.e. another `source` has
   a `calc_expression` referencing this gauge.name), confirm a live
   sibling source still produces the calc's input data_type.
   The orphan-source incident root-caused to this missing check.
4. Run `levels orphan-check` against the post-migration sandbox DB
   before committing the migration file.

### Reacting to an "orphan-check" pipeline alert
The post-build orphan-check (Phase 2b) appends to the pipeline's
`failures` list when a fetch-active source has no `gauge_source`
link. The email subject says the pipeline failed; the details live
in `journalctl -u kayak-pipeline`. Steps:
1. `levels orphan-check --json` (or `journalctl --since='1 hour
   ago' -u kayak-pipeline | grep orphan-check`) to enumerate.
2. For each orphan, decide and write the next migration:
   - **Link to a gauge** (preferred when the data is useful — see
     [[feedback_orphan_relink_over_deactivate]]): `INSERT OR IGNORE
     INTO gauge_source (gauge_id, source_id) VALUES (…, …);`.
   - **Deactivate the URL** only when the agency has retired it or
     the data is genuinely duplicative and unwanted.
   - **Delete the source row** only with the 0018-style observation
     re-pointing dance, and only if the row's history isn't worth
     preserving on a target gauge.

### Cross-links
- `CLAUDE.md` "Schema evolution" section gets a one-line
  cross-reference to this doc.
- `docs/operations.md` "post-pipeline failure" section gets a
  one-line entry pointing to the "Reacting to an orphan-check
  pipeline alert" subsection above.

## Out of scope

- **Schema-level FK / CHECK constraint** that mandates every
  fetch-backed source has a gauge_source link. SQLite triggers can't
  span tables cleanly for this shape and would block legitimate
  intermediate states (e.g., `INSERT INTO source` before `INSERT INTO
  gauge_source` inside a migration). Phase 2b's post-run check is
  cheaper and surfaces the same violation.
- **Deleting orphan source rows.** Once a row has observations,
  removing it requires the `0018`-style re-pointing dance. Deactivating
  the URL stops the bleeding; if a row truly needs to go, it gets its
  own migration. Out of scope for this plan beyond Phase 0's specific
  decisions.
- **Renaming `_auto_create_source` to express its dangers more
  clearly.** Considered, rejected: the function does exactly what its
  name says; the dangers are downstream.

## Verification gates (whole plan)

After Phase 0+1+2 land:

1. `levels orphan-check` on prod returns zero rows.
2. `pytest tests/test_pipeline.py::test_orphan_check_soft_fail` passes:
   - Synthesized orphan source flagged in `failures` list.
   - Pipeline exit code non-zero.
   - **Build artifact in `$OUTPUT_DIR` written despite the failure**
     (soft-fail invariant; protects against regressing to a
     short-circuiting design).
3. Manually break the invariant in a sandbox DB (`DELETE FROM
   gauge_source WHERE source_id = 220`); confirm `levels pipeline`
   exits non-zero, the build still completed, and the systemd
   OnFailure handler emails (verifiable by running the unit
   directly: `systemd-run --user --unit=orphan-test
   /home/pat/.venv/bin/levels pipeline` then checking mail spool).
4. Revert the sandbox change; confirm the pipeline runs clean again.

## Reproduce / dry-run

To reproduce the original symptom on a sandbox copy of the DB before
Phase 2 lands:

```bash
sqlite3 /tmp/kayak-sandbox.db ".restore '/home/pat/DB/kayak.db'"
sqlite3 /tmp/kayak-sandbox.db "DELETE FROM gauge_source WHERE gauge_id = 161;"
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db /home/pat/.venv/bin/levels fetch
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db /home/pat/.venv/bin/levels calculator
# observe: gauge 161 stays stale, no error surfaced
```

After Phase 2:

```bash
# same setup, then:
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db /home/pat/.venv/bin/levels pipeline
# observe: build completes (HTML written), exit code non-zero,
# stderr / journalctl shows the orphan-check ERROR with source 299
# and fetch_url 80, OnFailure handler emails pat.kayak@gmail.com.
```
