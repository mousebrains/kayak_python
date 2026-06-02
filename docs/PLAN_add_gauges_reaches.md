# Editing gauges and reaches — metadata via CSV + sync

**Status:** Active runbook (metadata-single-source). Supersedes the migration-based
add flow: metadata now lives **only** in the **`kayak_data`** repo's `*.csv` and
lands on prod via `levels sync-metadata`, matched by stable id. Covers **adding**,
**updating**, **splitting**, and **dropping** gauges and reaches.

> **Where the files are (data-repo split).** The CSVs + `reaches*.json` live in the
> separate `kayak_data` repo (the code reads it via `METADATA_DIR`); only schema
> migrations stay in the code repo. **Edit them via a PR to `kayak_data`.** All
> filenames below (`source.csv`, `reaches.json`, …) are relative to that repo's
> root, i.e. your `METADATA_DIR` clone.

## The model (one source of truth)

- **The `*.csv` *are* the metadata.** A change — add / edit / rename / remove a
  `source` / `gauge` / `reach` / junction row — is a reviewed **CSV diff**. There
  is no SQL data migration; `levels migrate` carries **schema** changes only
  (guard: `tests/test_scripts/test_migrations_schema_only.py`).
- **A new row takes a stable id** from `id_counters.csv`: read the table's
  `next_id`, use it, bump the counter. Ids **only ever increment** — a deleted id
  is never reused, so a `base62(id)` public handle never silently re-points.
  Guard: `tests/test_id_counters.py` (ids unique per table, every id `< next_id`).
- **FKs are the stable ids**, not names: `gauge_source.csv = gauge_id,source_id`;
  `reach.csv` carries `gauge_id`. You wire a new row by writing the id you just
  assigned — no "resolve by name" dance.
- **Apply path:** `scripts/deploy.sh` step 3.1 runs `levels sync-metadata` when
  `*.csv` changed: INSERT new / UPDATE changed / DELETE removed, **by id**,
  **preserving observations** (a rename is an UPDATE — the source's id never moves,
  so its observations stay valid). Deletes are gated behind `--allow-deletes`,
  which prints the per-source observation-drop counts first.
- **The two big reach blobs stay out of `reach.csv`:** `geom` →
  `reaches.json` (`import_metadata.py --geom-only`, deploy step 3.25);
  `gradient_profile` → `reaches-gradient.json` (`--gradient-only`, step
  3.26). They're large, machine-generated, and not regenerable on prod. `reach.huc`
  is tool-derived (`levels assign-huc`) but a single code, so it rides **in**
  `reach.csv` like any other column.

> **No more id race.** Because ids are author-assigned and stable, the old "the
> dev autoincrement id must equal the prod id, so don't let another reach land
> between trace and deploy" constraint is **gone**. You pick the id in the CSV; the
> same id keys `reaches.json` / `reaches-gradient.json`. Concurrent reach PRs only
> have to avoid grabbing the *same* `next_id` (the id-counter guard catches a
> collision at CI).

## Guard checklist (clear all of these, every metadata PR)

| Guard | Fails when | What we do |
|---|---|---|
| **id-counters** (`test_id_counters`) | a duplicate id, or `next_id` ≤ an existing id | each new row takes the current `next_id` and bumps it; never reuse a deleted id |
| **orphan-check** | a fetch-active `source` has no `gauge_source` link | always add the `gauge_source.csv` join row; sandbox-verify |
| **check-reaches** | `geom` has a `LINESTRING(` wrapper, <2 vertices, out-of-range coords, or endpoints drift >0.003° from the `lat/lon_start/end` columns | the tracer writes correct lon-first, no-wrapper geom; keep the endpoint columns in sync with the geom |
| **reach HUC** | a new/edited `reach` has NULL or hand-typed `huc` | run `levels assign-huc` on dev → 12-digit `reach.huc` + HUC8-name `reach.basin` into `reach.csv`. A NULL `huc` drops the reach from the watershed filter |
| **canonical `agency`** | a `source.agency` uses a raw parser slug | use `'USGS'` / `'WA DOE'` / `'NWRFC'` / `'USBR'` / `'Calculation'` etc. |
| **schema-only migrations** (`test_migrations_schema_only`) | a *metadata* change is written as a migration | metadata goes via CSV — a migration only appears here if you're **also** adding a column (schema), kept in `models.py` lockstep |

**The universal sandbox check** before every metadata PR — apply the CSV diff to a
fresh copy of prod and confirm the graph is clean:

```bash
cp /path/to/prod-or-fresh.db /tmp/sandbox.db
DATABASE_URL=sqlite:////tmp/sandbox.db levels sync-metadata --dry-run        # review the plan
DATABASE_URL=sqlite:////tmp/sandbox.db levels sync-metadata [--allow-deletes] # apply inserts + updates
DATABASE_URL=sqlite:////tmp/sandbox.db levels orphan-check                    # No orphan sources.
# geom/gradient apply, if the JSONs changed — run the two flags SEPARATELY (see warning below):
DATABASE_URL=sqlite:////tmp/sandbox.db python scripts/import_metadata.py --geom-only
DATABASE_URL=sqlite:////tmp/sandbox.db python scripts/import_metadata.py --gradient-only
DATABASE_URL=sqlite:////tmp/sandbox.db levels check-reaches                   # if reaches changed — run AFTER the geom apply (below)
```

> **Three things the first CSV-flow change (McKenzie split, 2026-06) surfaced:**
> 1. **`--geom-only` + `--gradient-only` together applies *both* JSONs** (still
>    skipping the CSV upsert). It *used* to load **neither** — each branch was
>    guarded by `not the_other_flag`, so they cancelled out into a silent no-op
>    ("TOTAL 0") — now fixed so the combined form does the obvious thing. The
>    snippet above runs them as two commands to mirror `deploy.sh` steps
>    3.25/3.26 (which apply each only if its JSON changed); a single combined
>    call now works too. (Plain `import_metadata.py` with no flags does the CSV
>    upsert **and** both JSONs.)
> 2. **`check-reaches` must run *after* the geom apply.** `geom` lives in
>    `reaches.json`, not `reach.csv`, so right after `sync-metadata` a re-traced
>    or **split** reach has its `lat/lon_*` endpoint columns moved but its geom
>    still the *old* shape → `check-reaches` fails with a multi-km endpoint
>    drift. Apply the geom JSON first, then check.
> 3. **`sync-metadata --dry-run`'s plan tallies INSERT/DELETE PKs only.** A pure
>    *update* (e.g. a flow-range or rename edit with no new/removed rows) shows
>    "no changes" in that summary yet **is** applied by the upsert pass — verify
>    updates by reading the row back, not by the dry-run count.

---

## Scenario: add a gauge (gauge-only)

A "gauge" is a `gauge` row + ≥1 `source` (+ a `gauge_source` link). Assign ids from
`id_counters.csv` and add the rows:

- **`gauge.csv`** — new `id` (bump `gauge` counter), `name` (UNIQUE),
  `display_name`, `sort_name`, `latitude`/`longitude`, `river`, `state`, `huc`, and
  (USGS) `usgs_id`.
- **`source.csv`** — new `id` (bump `source` counter), `name`, `agency`,
  `fetch_url_id` (or blank), `timezone`.
- **`gauge_source.csv`** — the join row `gauge_id,source_id` (the two ids above).
- **`fetch_url.csv`** — only for a fetch source: new `id`, `url` (UNIQUE),
  `parser`, `hours`, `is_active` — **and** the URL must also be in
  `data/sources.yaml` (the pipeline only fetches URLs present there).
- **`id_counters.csv`** — bump `next_id` for every table you added an id to.

**USGS is the easy case (zero extra wiring):** set `gauge.usgs_id`, add a source
`agency='USGS'`, `fetch_url_id` blank, named the digit station id, link it.
`fetch-usgs-ogc` then auto-fetches params `00060`/`00065`/`00010` (flow / gage /
**temperature**, °C→°F) for any gauge with `usgs_id`. No `fetch_url.csv` row,
nothing in `data/sources.yaml`.

**Fetch sources** (WA DOE `_WTM_`, USBR, USACE) additionally need the URL in
`data/sources.yaml`, and USACE temperature would first need `"Temp-Water":
DataType.temperature` added to `usace_cda.py::_PARAM_MAP` (its own small PR).

**State + HUC are required for the browser filter.** `gauges.html` emits a row's
state/watershed filter attributes only when **both** `gauge.state` and `gauge.huc`
(≥8 digits) are set — a gauge missing either is *unfilterable* and shows under every
state. So set `gauge.huc` (the 8-digit HUC8, from the USGS site's `huc_cd`) **and**
`gauge.state`. For a **border gauge** on a state-line river, set `gauge.state` as a
comma list (`OR,WA`): the build splits it into one `data-state` per state and
`filters.js` splits comma `data-state`, so the gauge filters under each — no
`gauge_state` table needed.

### Verify
- Sandbox sync + `levels orphan-check` → "No orphan sources."
- After prod deploy: `levels fetch-usgs-ogc` (USGS) populates flow/temp; the gauge
  filters under its state(s) in `gauges.html` (needs both `state` and `huc8`).

---

## Scenario: add a reach (reach-only)

The dev-only geometry toolchain (trace / DEM / `assign-huc` / elevations) is
**unchanged** — see [*The dev-only toolchain*](#the-dev-only-toolchain-prod-cant)
and [*Per-reach review*](#per-reach-review--coords--aw-metadata-cleanup) below. Only
the *delivery* changed: the computed values become CSV rows + JSON blobs, not a
migration.

Reach data splits across **three files by size:**

1. **Scalar metadata → `reach.csv`** (new `id` from the `reach` counter; keyed by
   `aw_id` in spirit, but the PK is the stable `id`):
   - `name='aw_<id>'`, `display_name`, `sort_name` (by put-in `elevation`,
     high→low ⇒ upstream→downstream — *not* `aw_id`), the four endpoint coords,
     `river`, `gauge_id` (the gauge's stable id), `description` (=section),
     `difficulties` (=class), `length`, `gradient`, `max_gradient`, `elevation`,
     `elevation_lost`, `aw_id`, plus **`huc` (12-digit HUC12) and `basin` (the HUC8
     name)** — from `levels assign-huc`, **not** hand-typed.
   - **`reach_state.csv`** (`reach_id,state_id`) — **required** or it's hidden from
     state filters.
   - **`reach_class.csv`** (`id`, `reach_id`, `name`) — **required** for the class
     pills; `name` NOT NULL; CHECK `low ≤ high`.
   - **`reach_guidebook.csv`** — link to the canonical state guide where indexed.
2. **`geom` → `reaches.json`** (large, lon-first `"lon lat,lon lat,…"`, no
   wrapper) → applied on prod by `import_metadata.py --geom-only` (deploy 3.25).
3. **`gradient_profile` → `reaches-gradient.json`** → `--gradient-only`
   (deploy 3.26). `max_gradient` (a scalar) stays in `reach.csv`.

`reaches.json` / `reaches-gradient.json` are keyed by `reach.id` — the **same**
stable id you assigned in `reach.csv` — so they line up by construction.

### Verify
- `levels check-reaches` (no wrapper, ≥2 vertices, endpoints within 0.003°).
- Render on the dev `reach.php` / `description.php` map; the trace PNG.

---

## Scenario: add a reach + gauge

Add the gauge (and its source/link) first, then the reach with `gauge_id` set to the
gauge's stable id. It's the union of the two scenarios above — assign all the ids
up front from `id_counters.csv` so the FKs resolve.

---

## Scenario: update gauge metadata

Edit the gauge's existing row in `gauge.csv` in place — `display_name`, `sort_name`,
`river`, `state`, `huc`, `latitude`/`longitude`, etc. The id is unchanged, so
`sync-metadata` applies it as an **UPDATE** and the gauge's observations are
untouched. (This is what the gauge-217 `sort_name` fix becomes: a one-line
`gauge.csv` edit, reviewed — no migration, no dual-edit.) `seed_gauge_display` is now
a CSV-*generation* helper: run it to draft the normalized `display_name`/`sort_name`,
review the diff, commit. No prod-DB mutation.

If a column you need doesn't exist yet, that's a **schema** change first (a migration
+ `models.py` in lockstep), then the values via `gauge.csv`.

### Verify
Sandbox sync; confirm the gauge still renders and (if `state`/`huc` changed) filters
correctly. `SELECT COUNT(*) FROM observation WHERE source_id = …` is unchanged.

---

## Scenario: update reach metadata

- **Scalar fields only** (`display_name`, `sort_name`, `river`, `description`,
  `difficulties`, `reach_class`/`reach_state`/`reach_guidebook` links): edit
  `reach.csv` (and the junction CSVs) in place. Same id → UPDATE.
- **Geometry changes** (endpoints moved, re-traced): re-run the toolchain and update
  the blobs:
  1. Update the four endpoint columns in `reach.csv` and re-trace (the geom must
     stay within 0.003° of the endpoints or `check-reaches` fails).
  2. Replace the reach's entry in `reaches.json` (geom) and
     `reaches-gradient.json` (gradient_profile).
  3. Recompute `elevation` / `elevation_lost` / `length` / `gradient` /
     `max_gradient` (`refresh_reach_elevations.py`, the DEM pipeline) and write them
     to `reach.csv`.
  4. If the **put-in** moved, re-run `levels assign-huc` (it's idempotent) — it may
     update `huc`/`basin` in `reach.csv`.

The cleanest way to regenerate the CSV + JSONs after dev edits is
`scripts/export_metadata.py` (writes `reach.csv` + both JSONs from the dev DB); diff
and commit.

### Verify
`levels check-reaches`; render the updated geom/profile on the dev map.

---

## Scenario: split a reach

A split is exactly **one update + one add**: shorten the existing reach to the new
boundary, and add a new reach for the downstream half.

1. **Pick the split point** on the dev map (the right-click lat/lon tool) — it is
   reach A's new take-out *and* reach B's put-in.
2. **Update reach A** (existing id): set its take-out to the split point; re-trace;
   recompute `length` / `elevation_lost` / `gradient` / `max_gradient`; replace its
   geom in `reaches.json` and gradient in `reaches-gradient.json`. (Per *update reach
   metadata*, geometry branch.)
3. **Add reach B** (new id from the `reach` counter, bump it in
   `id_counters.csv` — it isn't a DB table, so `export_metadata.py` won't touch
   it): put-in = the split point, take-out = the old downstream end; trace; full
   scalar metadata; its own `reach_state` / `reach_class` / `reach_guidebook`
   rows; geom → `reaches.json`, gradient → `reaches-gradient.json`. `gauge_id`
   may differ from A's if a different gauge governs the lower half.
   - **`reach.name` is UNIQUE** (partial index, `name IS NOT NULL`) — B needs a
     *distinct* `name` even though it shares A's `aw_id` (which is **not**
     unique). The McKenzie split used `aw_10888` (A) / `aw_10888b` (B).
   - **`reach_class.id` is not in `id_counters.csv`** — it plain-autoincrements,
     so a new class row takes `MAX(id)+1`.
   - **`basin_area`** tracks the *governing gauge's* `drainage_area` for ~⅔ of
     reaches — set each half to its own gauge's drainage where known (the split
     set A=348 to match its new McKenzie-Bridge gauge; B kept the old value, its
     South Fork gauge having no `drainage_area`).
4. **Re-derive both `huc`s** with `levels assign-huc` (their put-ins differ),
   **recompute the arc-length midpoint** (`reach.latitude/longitude`) for both —
   `recompute_midpoints.py` has no `--reach-ids`, so either scope it inline or
   accept that `--all` only rewrites already-drifted reaches — and **re-key
   `sort_name`** so A and B sit in upstream→downstream order with their
   neighbours (by put-in `elevation`, high→low). The split slotted B as
   `…aa aa 0a` between A (`…aa aa 0`) and the next reach down (`…aa aa 1`).

Both halves are independent rows after this — A keeps its id (and any inbound
references), B is brand-new. No observations are involved (reaches don't carry
observations; the gauge link does).

### Verify
`levels check-reaches` on both; the two segments abut at the split point with no
gap/overlap on the dev map.

---

## Scenario: drop a gauge

Remove the gauge's rows from `*.csv` and let `sync-metadata --allow-deletes`
apply the deletion by id:

- Remove the row(s) from `gauge.csv`, `source.csv`, and `gauge_source.csv` (and
  `fetch_url.csv` + the URL from `data/sources.yaml` if it's a fetch source — else
  `sync_sources` recreates the `fetch_url`). A USGS source has no URL to remove.
- **Pre-flight (per [`migrations.md`](migrations.md) *Removing a source safely*):**
  confirm nothing else needs the source — no `calc_expression` input, no
  `reach.gauge_id` — and if the source feeds a *calc* input on another gauge, relink
  that gauge to a live source first (the 0018/0020/0021 orphan-incident lesson).
- `sync-metadata` runs `observation`-first for a removed source (its FK is
  RESTRICT), then cascades `gauge_source` / `latest_*`. Without `--allow-deletes` it
  refuses and prints the observation-drop counts (deploy aborts until a human runs
  the delete by hand) — by design.

Deletions assign no id and the id counter never decrements, so the dropped id is
simply retired (never reused). No reconciliation dance — the CSV is the truth.

### Verify
Sandbox sync `--allow-deletes` + `levels orphan-check` → "No orphan sources." After
deploy: the gauge is gone from the build, and `fetch-usgs-ogc` no longer fetches it.
Worked example: Bridgeport (`12438000`) — historically migration `0071`, now a CSV
delete.

---

## The dev-only toolchain (prod can't)

These produce the values that go into the CSV/JSON — unchanged by the delivery
switch:

- **Trace:** `levels trace --putin LAT,LON --takeout LAT,LON --name "…"` under
  **brew python** (GDAL/osgeo, not `.venv`), against `Trace-cache/`. Emits the
  no-wrapper, lon-first geom string. **`levels trace` writes files** (`.csv`,
  `.geom.sql.txt`, `.png`) — it does **not** touch the DB. To land the geom +
  `length` (+ arc-length midpoint) into `reach`, run a small stage script that
  calls `kayak.tracing.trace.trace_reach` and `UPDATE`s the row — model:
  `docs/one-offs/import_mt_reaches.py` (Phase 2), or the McKenzie-split one-off
  `docs/one-offs/trace_mckenzie_split.py`.
- **Interpreter split (dev Mac).** No single interpreter has the whole stack:
  **trace** needs brew python (has `osgeo`, lacks `geopandas`); **`assign-huc`**
  needs `.venv` (has `geopandas`, lacks `osgeo`); elevations / DEM gradient /
  `build` / `export_metadata` / `sync-metadata` all run under `.venv`. Running
  `assign-huc` under brew python (or `trace` under `.venv`) fails on the missing
  import.
- **Elevation / elevation_lost / gradient:** `scripts/refresh_reach_elevations.py
  --reach-ids … --apply` (USGS 3DEP, httpx — dev-only).
- **`max_gradient` + `gradient_profile`:** the 3-stage `docs/one-offs/` DEM pipeline
  (`fetch_dem_tiles` → `sample_reach_elevations` → `compute_reach_gradient`),
  `DEM-cache/`.
- **`huc` + `basin`:** `levels assign-huc` (brew python — needs the `[geo]` extra and
  the WBD GPKG in `Trace-cache/`; prod can't run it). Point-in-polygons each put-in
  (`latitude_start`/`longitude_start`) → 12-digit HUC12 into `reach.huc`, HUC8 name
  into `reach.basin`; idempotent. Run it once endpoints are final, then read the
  resulting `huc`/`basin` off the dev DB into `reach.csv`.

**Source of truth for AW reaches: the `aw_reach` cache**
(`Gauge-metadata-cache/gauges.db`, populated by `match_aw_reaches.py`).
`docs/one-offs/import_mt_reaches.py` is the working stage-from-cache template.

## Per-reach review — coords + AW metadata cleanup

The refine loop is iterative — endpoints first on the map, then trace quality, then
names — and the final state matches what's served on the dev `description.php`
before any CSV row is written.

1. **Stage** the run on the dev DB from the `aw_reach` cache (AW's raw coords) so
   put-in/take-out markers render on the dev `reach.php`/`description.php`.
2. **Refine endpoints**: right-click any point on the dev map — `feature-map.js`
   exposes a contextmenu popup with the cursor lat/lon and a Copy button. The
   satellite base map is most accurate for channel placement; topo confirms named
   landmarks. Update the endpoint columns and re-trace.
3. **Re-trace and inspect** on the dev map. NHD HR is "the blue line on USGS topo,"
   so a clean trace follows the topo blue line. Common problems + fixes:
   - **Endpoint snapped to wrong flowline** (short straight detour at the put-in /
     take-out): nudge the endpoint a few metres onto the unambiguous main channel;
     re-trace.
   - **Trace orientation reversed** (a >100 m "jump" between consecutive vertices at
     a splice seam): reverse the segment before the jump.
   - **NHD routes through a side channel** (braided lowland / oxbow): use the splice
     in step 4.
4. **Trace splice through main-channel waypoints**: drop waypoints with the
   right-click tool (one every ~100–200 m through the problem stretch). Trace
   `pi → via1 → … → take-out` and stitch:
   - *Sparse* gaps (>500 m): `trace_reach` fills along NHD HR — clean for
     well-behaved stretches.
   - *Dense* waypoints (<200 m): join with a direct polyline; `trace_reach` between
     sub-200 m endpoints can route through a long NHD HR detour and inflate length
     5–10×.
   - The hybrid (NHD HR on long gaps, polyline on dense groups) produced reach 417's
     final geom.
5. **DEM channel-min snap** (canyon-shaped reaches only):
   `docs/one-offs/snap_reach_to_channel_min.py` walks each vertex perpendicular to
   flow, samples the DEM (1 m LIDAR if cached, else 10 m 3DEP — WA/OR/ID), nudges to
   the local minimum. Dry-run first; **apply** when mean drop ≥ ~10 ft and snap rate
   ≥50% (canyons), **skip** when ≤ ~4 ft / ≤30% (braided lowland — at the 10 m DEM's
   ~8 ft RMSE noise floor).
6. **Compute elevation + gradient_profile** once geoms are final:
   `refresh_reach_elevations.py --apply` (3DEP EPQS), then
   `sample_reach_elevations.py` + `compute_reach_gradient.py --apply`. `sig_frac
   ≥75%` per reach indicates real signal. Reservoir-ending reaches produce a
   trailing non-significant bar; the plot keeps bars ≥0.5 mi wide.
7. **Normalize names** — see *Naming and AW cleanup* below.
8. **Final scalar metadata → `reach.csv`**; **geom → `reaches.json`**;
   **`gradient_profile` → `reaches-gradient.json`**; **`reach_guidebook.csv` rows**
   linking each reach to its canonical state guide.

### Naming and AW cleanup

AW's `river`/`display_name`/`description` vary run-to-run; normalize during the
review pass:

- **`river`**: one canonical value per **branch** so reaches group in the table. For
  sibling forks that share a basin, **share the `river`** and let `display_name`
  discriminate (`river='Lewis'` for both NF and EF; `display_name='NF Lewis'` /
  `'EF Lewis'`). Tribs with their own river name keep it.
- **`description`**: bare location ("Twin Falls to FR 88"), no leading section number
  (`1.`), no trailing parenthetical (`(Upper)`) — the number is implicit in
  `sort_name`, parentheticals belong in `notes`.
- **`sort_name`**: Sandy-basin convention `<Basin> <letter> NN <section>`. Within a
  basin the letter is the branch (NF=`a`, EF=`b`, sub-tribs=`c` on); NN is the
  section sequence ordered upstream→downstream by put-in `elevation` descending. Use
  a two-letter suffix to slot after an existing single-letter sort (`Canyon ae 01`).
- The **linked gauge** gets the same `river`/`location`/`display_name` cleanup. Gauge
  `sort_name` follows the pipe-delimited convention
  `<river>|<branch-code>|<NNNNNN>|<NNNNNN>` (branch-code `9` for mainstem, `0<short>`
  for sub-rivers).
- **`reach_guidebook`**: link each reach to its guidebook with `page` + `run`; skip
  reaches that aren't indexed.

`reach` has no `location` column — its geographic "location" is the put-in/take-out
coords.

---

## Executed batches (historical — via the old migration flow)

These initial batches predate the CSV+sync flow and landed as SQL migrations; the
rows now live in the CSVs (reconciled by snapshot). Kept for reference:

- **Batch A — Columbia mainstem corridor** (gauge-only): the temperature subset —
  4 gauges (Bridgeport, below John Day Dam, The Dalles, `Bonneville_merge`) via
  migration `0066` (source-based fetcher refactor in companion `0065`); the lower
  NWS stage gauges (Vancouver `VAPW1`, St. Helens `SHNO3`) via `0070`; USACE dam
  flow/temp via `0069`. All carry `huc8`; the three border gauges below McNary set
  `state='OR,WA'`.
- **Batch B — WA Lewis system** (reach+gauge): 2 new USGS gauges (NF Lewis
  `14216000`, Canyon Creek `14219000`) + 12 reaches + their state/class/guidebook
  rows via migration `0067`; `gradient_profile` from the DEM pipeline; geoms refined
  via the right-click loop with waypoint splice on EF Lewis §5 and DEM channel-min
  snap on the four canyon reaches.

The full corridor survey tables and per-reach details are preserved in git history
(this file pre-Phase-5) and in the migrations themselves (`0065`–`0070`).
