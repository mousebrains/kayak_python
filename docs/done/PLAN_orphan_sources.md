# Plan — Stop fetch silently feeding orphan sources

**Status:** Closed (2026-05-15). All 4 phases shipped:
- **Phase 0** — `data/db/migrations/0021_resolve_orphan_sources.sql` (commit `5a876a0`): linked the 5 known orphan sources (29C100 STG+WTM, 28B080 STG+WTM, WASW1) to gauges 150 and 184.
- **Phase 1** — `levels orphan-check` CLI + `src/kayak/db/sources.py::find_orphan_sources()` (commit `1699631`). Tests in `tests/test_db/test_sources.py` and `tests/test_cli/test_orphan_check.py`.
- **Phase 2** — auto-create ERROR escalation (`src/kayak/parsers/base.py:209-232`) + post-build orphan-check pipeline step (`src/kayak/cli/pipeline.py:109-132`); both in commit `bd25db4`. Soft-fail behavior verified in `tests/test_cli/test_pipeline.py::test_orphan_check_soft_fail`.
- **Phase 3** — `docs/migrations.md` (commits `89b47a6`, `fc75c50`). Cross-linked from `CLAUDE.md` "Schema evolution:" block.

Live DB confirms zero orphan rows (`find_orphan_sources()` returns `[]`).

Two follow-up areas were reviewed 2026-05-15 and closed out:
- **Adjacent graph-health checks** — three sibling invariants (active `fetch_url` with no source; gauge with no `gauge_source` link; reach with no `gauge_id`) — confirmed intentional design states rather than bugs. Providers come and go but the gauge history is worth preserving; not every reach has a monitored gauge. See `docs/migrations.md` § "Adjacent graph-health checks" for the residual calc-expression-resolution surface that remains future-work-worthy.
- **`init-db`'s missing `gauge_source` seed path** — left as future work in `docs/migrations.md`; indirectly relevant to the Tier 1 restore drill.

Original draft context preserved below for historical reference. The plan targeted the systemic bug behind the 2026-05-11 incident: auto-create mints `source` rows without `gauge_source` links, so when a deletion migration removes a source but leaves its `fetch_url` active, the next pipeline run silently rebuilds an orphan that fetches forever into a dead end.

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
>     emits only `gauge` (no flow conflict), and assumed the DSG
>     endpoints once-emitted-then-dropped `gauge`+`temperature` —
>     wrong; see iter 2.
> - iter 9 (2026-05-14, stopping): 0 substantive findings.
>   Re-read top to bottom plus a live API spot-check (WASW1 NWPS
>   returns Flow=secondary but currently sentinels -999; source 298
>   stays gauge-only in practice — plan's claim accurate). Phase 0
>   row 298 rationale "secondary feed" framing is slightly imprecise
>   (both 296 and 298 emit `gauge` at the same cadence, neither is
>   "primary") but not worth a churn-edit. Convergence:
>   4 → 8 → 8 → 1 → 4 → 1 → 2 → 1 → 0 findings.
> - iter 8 (2026-05-14): 1 finding.
>   - **Wiki-style `[[memory-link]]` syntax removed.** The doc
>     committed in git can't resolve those backlinks (the memory
>     system isn't checked in). Replaced the
>     `[[feedback_orphan_relink_over_deactivate]]` reference in
>     Phase 3 with an inline rationale (one sentence: "prefer linking
>     fetch-active orphans into a gauge over deactivating the URL —
>     live data is cheap to keep wired and expensive to lose").
> - iter 7 (2026-05-14): 2 findings.
>   - **Fresh-DB caveat noted.** `init-db` doesn't seed
>     `gauge_source` from anywhere — only migration 0020 contains
>     `INSERT INTO gauge_source` across all 20 migrations. A brand-new
>     `init-db` + `migrate` DB has zero `gauge_source` rows, so
>     `levels orphan-check` would flag every fetch-backed source
>     (~300) as an orphan. This is a real gap in the project but
>     orthogonal to this plan (which addresses "auto-create produces
>     orphans after a deletion migration"). Documented as a known
>     limit in the Phase 1 wiring section so the developer running
>     against a pristine DB isn't surprised.
>   - **Adjacent graph-health checks called out as future work.**
>     "Active fetch_url with no sources", "gauge with no sources",
>     "reach with no gauge" are sibling invariants that the
>     `find_orphan_sources` shape generalizes to. Listed in Out of
>     scope so a future plan can pick them up without re-discovering
>     the pattern.
> - iter 6 (2026-05-14): 1 finding.
>   - **`update-gauge-cache` isn't a top-level CLI command** (verified
>     against `levels --help`); it's only an internal pipeline step.
>     Phase 0 verification text was treating it as runnable directly.
>     Reworded to `levels pipeline --skip-fetch` (does the full
>     downstream chain: calc-rating → update-gauge-cache → calculator
>     → build) or the Python one-liner `python -c "from
>     kayak.db.cache import update_all_latest_gauges ..."`.
> - iter 5 (2026-05-14): 4 findings; one is a real query bug.
>   - **Orphan SQL needs `MAX(lo.observed_at)`.** With
>     `latest_observation`'s primary key `(source_id, data_type)` and
>     a `LEFT JOIN` against it, a source with multiple data_types
>     produces N rows pre-GROUP BY. `GROUP BY s.id` collapses to one,
>     and without an aggregator SQLite's lax-aggregation rules pick
>     an arbitrary row's `lo.observed_at` for display — not always
>     the freshest. Today's 5 orphans each have a single data_type so
>     the bug is invisible; future orphans (e.g., a multi-data-type
>     wa.gov DSG source losing its link) would display a misleading
>     "latest" value. Switch the SELECT to
>     `MAX(lo.observed_at) AS latest_obs`.
>   - **Phase 2a wording: "no DB query needed".** Auto-create itself
>     does an INSERT (`session.add(src); session.flush()`). Reworded
>     to "no additional read query" so the meaning is unambiguous.
>   - **Verification gate #3 simplification.** "Pick a gauge with
>     multiple linked sources" was unnecessary precision; dropping any
>     `gauge_source` row creates an orphan, regardless of the gauge's
>     remaining link count.
>   - **Phase 0 row-298 rationale soften.** Cited specific 5.02 /
>     5.05 ft values tie the doc to today's snapshot. Reframed as
>     "two independent sensors at the same site can read a few
>     hundredths of a foot apart."
> - iter 4 (2026-05-14): 1 substantive finding (others trivial /
>   already-handled).
>   - **Phase 3 deactivation has a cheaper path than a migration.**
>     `sync_sources` runs at the head of every `levels fetch`
>     (`src/kayak/cli/fetch.py:150`); it sets `is_active=0` on any
>     fetch_url whose URL isn't in `data/sources.yaml`
>     (`init_db.py:131-144`). And the actual fetch iterates
>     `load_sources()` — URLs not in YAML aren't fetched, period;
>     `is_active` is an audit marker, not the gate. So the
>     migration-doc step "deactivate the URL when no other source
>     consumes it" can be done by **removing the URL from
>     `data/sources.yaml`** instead of writing an `UPDATE` in the
>     migration. Documented as the preferred alternative in Phase 3.
> - iter 3 (2026-05-14): 8 findings from re-reading the plan whole
>   plus a verification pass against tests/, the orphan query
>   wall-clock, and the SQLite FK pragma.
>   - **Phase 0 row rationale phrasing inconsistent.** Rows 294/296
>     say "since 2026-05-11" implying the URL once carried gauge —
>     it never did. Fixed to "only path going forward" matching the
>     body.
>   - **Verification gate #3 `systemd-run --user` is wrong.** The
>     OnFailure handler is registered on the system unit, not a user
>     unit, and running as user can't hit it. Also can't sudo. Dropped
>     the systemd-trigger gate; rely on pipeline-level `SystemExit(1)`
>     for Phase 2 verification, trust the unchanged OnFailure wiring
>     for the email/ntfy half.
>   - **Reproduce expected output incorrect.** Deleting source 182
>     leaves gauge 150 with **zero** linked sources, so
>     `update_all_latest_gauges` deletes its `latest_gauge_observation`
>     row (cache.py:163-170). The pipeline doesn't "leave gauge 150
>     pegged to 2026-05-11"; it removes its rows entirely. Updated.
>   - **Reproduce needs `PRAGMA foreign_keys = ON`.** Verified the
>     sqlite3 CLI defaults to FK=OFF; the cascade-via-CASCADE on
>     gauge_source's source_id only fires if foreign_keys is
>     explicitly enabled in the script.
>   - **`OrphanRow` contract was undefined** but referenced in the
>     Phase 2b code block. Pinned to a tuple/NamedTuple of
>     (source_id, name, agency, url, is_active, latest_obs).
>   - **Performance claim corrected.** Measured the orphan query at
>     3 ms on the live DB (568 MB, 302 sources, 5 orphans). Plan
>     said "<50ms"; updated to "~3 ms measured".
>   - **Test paths fixed.** `tests/test_pipeline.py` doesn't exist —
>     `tests/test_cli/test_pipeline.py` does. New
>     `find_orphan_sources` unit test belongs in
>     `tests/test_db/test_sources.py` (new file).
>   - **Cross-link line numbers dropped.** `CLAUDE.md:182` and
>     `docs/operations.md:199` will drift as those docs evolve;
>     reference by section heading only.
> - iter 2 (2026-05-14): 8 findings from a code-walk against `main`
>   at `a86541b`.
>   - **Phase 0 story corrected.** The DSG endpoint (verified via
>     curl) emits only `Discharge (cfs)` — it never carried gauge
>     height or temperature. The 7,321 `gauge` + 7,317 `temperature`
>     rows on source 220 with min `observed_at = 2025-10-01` are
>     history-merged from migration 0018's `INSERT OR IGNORE FROM
>     source_id IN (178, 218, 219)`. They stop at 2026-05-11 because
>     0018 ran then and no fresh data has flowed in since. The orphan
>     STG/WTM sources are not "the only fresh source" — they're the
>     **only source, period**, for those data_types on gauges 150 +
>     184 going forward. Strengthens the Phase 0 argument.
>   - **Cache citation fixed.** `update_all_latest_gauges` is at
>     `src/kayak/db/cache.py:309`, not `:230+`.
>   - **Phase 2b implementation made concrete.** `_orphan_check`
>     raises an exception when orphans are found; pipeline.py's
>     existing `except Exception` at line 111-113 catches it,
>     appends to `failures`, and `raise SystemExit(1)` at line 138
>     fires the OnFailure handler. No new failure-recording machinery.
>   - **Phase 2a simplified.** The "URL has no other live source"
>     check doesn't need a DB query — `_auto_create_source` already
>     has `self.source_map` (built from `fetch_url.sources` at
>     `src/kayak/cli/fetch.py:206-208`); empty map = URL orphaned of
>     sources. Eliminates one query per auto-create.
>   - **Phasing constraint added.** Phase 2b must not land before
>     Phase 0 — the 5 known orphans on prod would trigger the alert
>     on every pipeline run. Phase 1 (the CLI) is read-only and can
>     land anytime.
>   - **`find_orphan_sources()` location moved.** Was
>     `src/kayak/cli/orphan_check.py`; should be
>     `src/kayak/db/sources.py` (existing module). CLI imports from
>     db, not the other way around — avoids circular imports when
>     Phase 2b's pipeline step also imports it.
>   - **Reproduce script rewritten.** Original deleted
>     `gauge_source WHERE gauge_id = 161`, which doesn't reproduce
>     the auto-create path — source 299 still exists and just keeps
>     fetching to itself. To exercise auto-create you must delete the
>     Source row AND have an active fetch_url. New recipe uses an
>     intentionally-deleted sibling to simulate the post-0018 state.
>   - **Operations doc cross-link target found.**
>     `docs/operations.md:199 ## Pipeline failure triage` is the
>     correct anchor, not "post-pipeline failure".
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
DSG endpoint emits only `Discharge (cfs)` (verified via curl against
`…28B080_DSG_FM.TXT` — three header columns: DATE, TIME, Discharge).
The `gauge` and `temperature` observations on source 220
(min `observed_at = 2025-10-01`, max `= 2026-05-11`) were merged in by
migration 0018's `INSERT OR IGNORE` from now-deleted sibling sources
(178 WASW1 + 218/219 28B080 duplicates). Once 0018 ran on
2026-05-11, no new fresh data of those types has flowed in — the
URL doesn't carry them. The orphan STG/WTM URLs are therefore the
**sole source, period**, for `gauge` and `temperature` on gauges 150
and 184 going forward. Without Phase 0, those two data_types die.

Confirmation from the live cache:

| gauge | data_type   | latest_gauge_observation | only live source for this data_type |
|-------|-------------|--------------------------|--------------------------------------|
| 150   | flow        | 2026-05-14 16:15         | source 182 DSG (linked)               |
| 150   | gauge       | **2026-05-11 20:15**     | source 294 STG (orphan — STG URL only emits gauge) |
| 150   | temperature | **2026-05-11 20:15**     | source 295 WTM (orphan — WTM URL only emits temp)  |
| 184   | flow        | 2026-05-14 16:15         | source 220 DSG (linked)               |
| 184   | gauge       | **2026-05-11 21:15**     | source 296 STG (orphan, 5.05ft) + source 298 NWPS (orphan, 5.02ft) |
| 184   | temperature | **2026-05-11 20:15**     | source 297 WTM (orphan) |

Phase 0 isn't a "speed-up the freshness" change — for `gauge` and
`temperature` on these two gauges, it's restore-or-lose. Linking
the 5 orphan sources turns 6 frozen-since-2026-05-11 data_type rows
back into live readings.

Code surfaces:
- `src/kayak/parsers/base.py:165` — `_auto_create_source` (the
  silent-orphan factory; warns but doesn't block).
- `src/kayak/parsers/base.py:143-153` — caller in `dump_to_db`.
- `src/kayak/cli/fetch.py:206-208` — `source_map` is built from
  `fetch_url.sources`; an empty map at parse time is exactly the
  "URL orphaned of sources" condition Phase 2a needs to detect.
- `src/kayak/db/cache.py:149-239` — `update_latest_gauge` filtering on
  `gauge_source.source_id` (per-gauge refresh).
- `src/kayak/db/cache.py:309` — `update_all_latest_gauges` (bulk
  refresh via window query; same `JOIN gauge_source` filter at
  line 255).
- `src/kayak/cli/pipeline.py:71-141` — pipeline DAG. Step funcs run
  inside `try/except` at lines 107-113; a step that wants to record
  failure raises an exception. Lines 133-138 turn a non-empty
  `failures` list into `SystemExit(1)`. systemd OnFailure handler
  fires from there.
- `src/kayak/cli/init_db.py:49+` — `sync_sources` (pre-creates source
  rows from `data/sources.yaml` `stations:` blocks but does **not**
  create `gauge_source` links; that's the structural reason
  auto-create produces orphans).
- `systemd/kayak-pipeline.service:4` — `OnFailure=kayak-notify-failure@%n.service`.
- `systemd/kayak-notify-failure@.service` — the existing email +
  ntfy.sh + syslog handler this plan reuses.
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
| 294 | link to gauge 150 (29C100) | only path for `gauge` on this gauge going forward (DSG URL doesn't carry it); not a redundancy add — a restoration |
| 295 | link to gauge 150           | only path for `temperature` going forward |
| 296 | link to gauge 184 (28B080) | only path for `gauge` going forward |
| 297 | link to gauge 184           | only path for `temperature` going forward |
| 298 | link to gauge 184 as secondary `gauge` feed | WASW1 NWPS-only feed kept as a sibling to 296's wa.gov STG; insert-into philosophy. Both emit `gauge` at ~15-min cadence from independent sensors at the same site, so the latest cached value can flicker by a few hundredths of a foot (whichever feed updates last wins per `update_latest_gauge`'s ORDER BY `observed_at` DESC, source_id DESC). Cosmetic; flagged for awareness |

Migration shape:

```sql
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (150, 294);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (150, 295);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 296);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 297);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 298);
```

Verification: `levels migrate`, then run the orphan query (Phase 1) and
confirm it returns zero rows. Refresh the caches with `levels pipeline
--skip-fetch` (runs calc-rating → update-gauge-cache → calculator →
build) — `update-gauge-cache` itself isn't a top-level CLI. Confirm:
- gauges 150 + 184 `gauge` and `temperature` rows in
  `latest_gauge_observation` are now today's date, not still pegged at
  2026-05-11;
- gauge 184 `gauge` reflects the freshest of the two participating
  feeds (sanity check that both 296 and 298 are contributing — their
  `source_id` should alternate as the freshest in
  `latest_gauge_observation` over successive runs);
- no regression on `flow` (DSG sources stay authoritative).

## Phase 1 — Detection: `levels orphan-check` (1 CLI, ~30 min)

A new top-level subcommand that prints (and optionally exits non-zero
on) any active source whose data path doesn't reach a gauge. Use the
exact query that built the table above:

```sql
SELECT s.id, s.name, s.agency, fu.url, fu.is_active,
       MAX(lo.observed_at) AS latest_obs
FROM source s
LEFT JOIN gauge_source gs ON gs.source_id = s.id
LEFT JOIN fetch_url fu ON fu.id = s.fetch_url_id
LEFT JOIN latest_observation lo ON lo.source_id = s.id
WHERE gs.source_id IS NULL
  AND s.fetch_url_id IS NOT NULL
  AND (fu.is_active = 1 OR lo.observed_at > datetime('now', '-7 days'))
GROUP BY s.id;
```

`MAX(lo.observed_at)` (not bare `lo.observed_at`) handles sources
with multiple data_types in `latest_observation` — each contributes
one row pre-GROUP BY; we want to display the freshest, not a SQLite
lax-aggregation pick.

The `is_active = 1 OR latest_obs > 7d` clause captures both "fetch is
still firing" and "fetch was deactivated very recently and a stale
orphan is hanging around."

Wiring:
- `src/kayak/db/sources.py` — add:
  ```python
  class OrphanRow(NamedTuple):
      source_id: int
      name: str
      agency: str | None
      url: str
      is_active: bool
      latest_obs: datetime | None

  def find_orphan_sources(session: Session) -> list[OrphanRow]: ...
  ```
  Living in the `db/` layer (alongside the existing
  `get_negative_flow_source_ids`) keeps the CLI and pipeline both
  importing downward, with no circular-import risk.
- `src/kayak/cli/orphan_check.py` — thin `addArgs` + `orphan_check(args)`
  wrapper that calls the db helper and formats output. Same shape as
  `decimate.py`.
- `src/kayak/cli/main.py` — import + register.
- Flag `--exit-nonzero-if-found` for ad-hoc CI / scripting use.
- Output: human table by default; `--json` for tooling consumers.

Scope note: this check assumes a populated DB. A brand-new
`init-db` + `migrate` DB has zero `gauge_source` rows
(`init-db` doesn't seed them from anywhere; only migrations do,
and only one migration today contains `INSERT INTO gauge_source`).
The CLI run against such a DB would flag every fetch-backed
source as an orphan — accurate but not actionable. Filling in
the missing `gauge_source` seed path is a separate gap, out of
scope here.

Verification:
- After Phase 0 lands, `levels orphan-check` returns zero rows on
  prod.
- Synthetic unit test in **`tests/test_db/test_sources.py`** (new
  file): insert a Source with `fetch_url_id` set and no
  `gauge_source` row, assert `find_orphan_sources(session)` returns
  exactly that source.
- CLI test in **`tests/test_cli/test_orphan_check.py`** (new file):
  same fixture, assert the CLI exits non-zero with
  `--exit-nonzero-if-found` and zero without.
- Integration smoke (ad hoc, not in CI): copy the live DB to a
  sandbox, INSERT a fake Source row with a fetch_url, run the CLI,
  see the row.

## Phase 2 — Prevention: loud auto-create + post-build report (~45 min)

Two layered fixes, both small, both independent.

**2a. `_auto_create_source` escalates when its URL has no other live
source.** The information is already in hand: `self.source_map` was
built at `src/kayak/cli/fetch.py:206-208` from `fetch_url.sources`. At
auto-create time, two cases:
1. `self.source_map` non-empty — multi-station feed (USGS basin
   queries, the wa.gov station dirs); auto-create is legitimate.
   Keep current `WARNING` log.
2. `self.source_map` empty — the URL has zero live sources. This is
   the post-deletion-migration case. Mint the row so the parser
   doesn't crash mid-batch, but log at `ERROR` with both the new
   `source.id` and `self.url` to help the operator decide between
   link-to-gauge and deactivate-url.

No additional read query (auto-create still does its own INSERT);
no sentinel file. The ERROR log gets caught by Phase 2b's
end-of-pipeline check on the same run, so a one-shot log line is
enough — no need for a "did this fire last run?" trail.

**2b. End-of-pipeline orphan-check, soft fail.** Append a final step
to the pipeline DAG (after `build`) in `src/kayak/cli/pipeline.py`:

```python
def _orphan_check(args: argparse.Namespace) -> None:
    from kayak.db.engine import get_session
    from kayak.db.sources import find_orphan_sources

    session = get_session()
    try:
        rows = find_orphan_sources(session)
        if rows:
            logger.error("Orphan-check found %d unlinked fetch-active source(s):", len(rows))
            for r in rows:
                logger.error("  source.id=%d name=%s url=%s latest=%s",
                             r.source_id, r.name, r.url, r.latest_obs)
            raise RuntimeError(f"{len(rows)} orphan source(s) found — see ERROR logs above")
    finally:
        session.close()

steps.append(("orphan-check", _orphan_check))
```

The pipeline's existing `try/except Exception` at
`src/kayak/cli/pipeline.py:107-113` catches the `RuntimeError`, logs
it, and appends `("orphan-check", "<msg>")` to the `failures` list.
Lines 133-138 then turn the non-empty failures list into
`raise SystemExit(1)` — at which point systemd marks
`kayak-pipeline.service` as failed, fires its
`OnFailure=kayak-notify-failure@%n.service`
(`systemd/kayak-pipeline.service:4`), and the existing
`kayak-notify-failure@.service` runs:
- `logger -t kayak-alert -p user.err` → syslog
- `mail -s "Kayak: kayak-pipeline.service failed" pat.kayak@gmail.com`
- `curl … ntfy.sh/$NTFY_TOPIC` if `NTFY_TOPIC` is set

No new email plumbing; no new exception class.

**Important:** soft fail, not short-circuit. The build runs to
completion *before* orphan-check (it's appended after `build` in the
steps list), so the public site stays fresh on the data we *do*
have. Only the notification fires.

**Phasing constraint:** Phase 2b must not land before Phase 0. The
5 known orphans on prod would otherwise trigger the alert on every
pipeline run until Phase 0's migration cleared them. Phase 1's
read-only CLI can land in either order.

The notification email subject reads "Kayak: kayak-pipeline.service
failed"; details (the orphan list) live in `journalctl -u
kayak-pipeline`. That's the same operator workflow as any other
pipeline failure, so no new runbook is needed beyond Phase 3's
migrations doc.

Cost: one extra query per pipeline run (~3 ms measured on the live
568 MB DB with 302 sources; the existing `LEFT JOIN` indexes carry
it).

Test the failure path: extend **`tests/test_cli/test_pipeline.py`**
(existing file) with a `test_orphan_check_soft_fail` case —
synthesize the "new orphan in this run" state, assert the pipeline
records the orphan-check failure in the failures list and exits
non-zero, **but also asserts the build artifact was written**
(proving soft-fail behavior).

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
   - Another live source still consuming it? Leave it alone.
   - No other consumer, and the URL is genuinely done? **Preferred:**
     remove the URL from `data/sources.yaml`. `sync_sources`
     (called at the head of every `levels fetch`,
     `src/kayak/cli/fetch.py:150`) will flip `is_active=0` on the
     next run, and the fetch loop already skips URLs not in YAML —
     `is_active` is just an audit marker, not the gate. The
     migration touches only the source-row table.
   - No other consumer, but the URL must stay in YAML (e.g.,
     stations: block references it for TZ data)?
     `UPDATE fetch_url SET is_active = 0 WHERE id = …` in the
     migration, matching the 0019 pattern. The 0018 anti-pattern
     (delete sources, touch nothing else) is what caused the May
     2026 orphan incident.
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
   - **Link to a gauge** (preferred when the data is useful — live
     data is cheap to keep wired and expensive to lose; deactivating
     a URL only to have auto-create re-orphan it next deploy is the
     mistake we're trying to avoid): `INSERT OR IGNORE INTO
     gauge_source (gauge_id, source_id) VALUES (…, …);`.
   - **Deactivate the URL** when the agency has retired it or the
     data is genuinely duplicative and unwanted. Preferred path:
     remove the URL from `data/sources.yaml` (the next `levels
     fetch` flips `is_active=0` automatically via `sync_sources`).
     Use an explicit `UPDATE fetch_url SET is_active = 0` migration
     only if the URL must stay in the YAML for other reasons.
   - **Delete the source row** only with the 0018-style observation
     re-pointing dance, and only if the row's history isn't worth
     preserving on a target gauge.

### Cross-links
- `CLAUDE.md` "Schema evolution:" bullet block gets a one-line
  cross-reference to this doc.
- `docs/operations.md` "## Pipeline failure triage" section gets a
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
- **Adjacent graph-health checks.** The `find_orphan_sources` shape
  generalizes to several other invariants worth a future plan:
  active `fetch_url` with zero sources, `gauge` with zero linked
  sources, `reach` with no `gauge_id`. Each is a different "the
  graph is broken" surface; same one-row-per-violation reporting
  shape. Out of scope here so this plan stays focused on the
  auto-create-orphan path.
- **`init-db`'s missing `gauge_source` seed path.** No migration
  except 0020 inserts into `gauge_source`, so rebuilding prod from
  scratch via `init-db && migrate` cannot recreate the live link set.
  Real gap, but orthogonal — this plan is about preventing future
  orphans, not about fresh-DB seeding.

## Verification gates (whole plan)

After Phase 0+1+2 land:

1. `levels orphan-check` on prod returns zero rows.
2. `pytest tests/test_db/test_sources.py::test_find_orphan_sources` and
   `pytest tests/test_cli/test_pipeline.py::test_orphan_check_soft_fail`
   both pass. The pipeline test asserts:
   - Synthesized orphan flagged in `failures` list.
   - Pipeline exit code non-zero.
   - **Build artifact in `$OUTPUT_DIR` written despite the failure**
     (soft-fail invariant; protects against regressing to a
     short-circuiting design).
3. Sandbox induction: delete any one `gauge_source` row in the
   sandbox DB, run `levels pipeline` against it. Confirm exit code
   1 and the build artifact present in `$OUTPUT_DIR`. Stops short of
   asserting `OnFailure` ran — that wiring is in the unchanged unit
   file and verified once during deploy via the regular
   pipeline-failure path. (Manual sudo-required end-to-end
   verification isn't in scope for routine plan execution.)
4. Revert the sandbox change; confirm the pipeline runs clean again.

## Reproduce / dry-run

The `DATABASE_URL` env-var override works (verified:
`load_dotenv()` defaults to `override=False`, so a pre-set env var
takes precedence over `.env`'s `DATABASE_URL=sqlite:////home/pat/DB/kayak.db`).

To exercise the auto-create-orphan path you need to recreate the
post-0018 state: the `fetch_url` row exists and is_active=1, but the
`source` row that linked it to a gauge has been deleted. Picking an
arbitrary already-linked station (29C100, source 182, fetch_url 72,
gauge_source link to gauge 150):

```bash
sqlite3 /tmp/kayak-sandbox.db ".restore '/home/pat/DB/kayak.db'"

# Simulate "migration deleted the source but left the URL active."
# The CLI defaults to PRAGMA foreign_keys=OFF, so enable them
# explicitly or the gauge_source CASCADE won't fire and observation
# RESTRICT won't enforce. observation FK is ON DELETE RESTRICT, so
# re-point obs to a dummy sibling first (any other source row will
# do; using 220 here).
sqlite3 /tmp/kayak-sandbox.db <<'SQL'
PRAGMA foreign_keys = ON;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 220, observed_at, data_type, value FROM observation WHERE source_id = 182;
DELETE FROM observation WHERE source_id = 182;
DELETE FROM source WHERE id = 182;  -- gauge_source row cascades
SQL

# Before Phase 2: pipeline succeeds silently. The deletion of source
# 182 leaves gauge 150 with zero gauge_source links; the next
# update-all-latest-gauges deletes gauge 150's latest_gauge_observation
# rows entirely (cache.py:163-170). Fetch then auto-creates a fresh
# orphan source row for station 29C100 against fetch_url 72.
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db /home/pat/.venv/bin/levels pipeline
# observe: exit 0, no email, no ntfy, gauge 150 has no rows in
# latest_gauge_observation, a new orphan source row exists for the
# 29C100 station with no gauge_source link.

# After Phase 2: pipeline build still completes, then orphan-check
# raises; failures list non-empty; exit 1.
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db /home/pat/.venv/bin/levels pipeline
# observe: build artifact written to $OUTPUT_DIR, exit code 1,
# stderr shows orphan-check ERROR naming the new source.id and
# fetch_url. (OnFailure → email/ntfy is the production-only half;
# this CLI invocation runs outside systemd and doesn't trigger it.)
```

Cleanup: `rm /tmp/kayak-sandbox.db /tmp/kayak-sandbox.db-wal /tmp/kayak-sandbox.db-shm`.
