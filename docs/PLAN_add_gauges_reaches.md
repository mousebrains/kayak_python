# Adding gauges and reaches — methodology + initial batches

**Status:** In progress (2026-05-27). Active runbook for adding new data — *not* a
completed/archived plan (keep in `docs/`, not `docs/done/`).

## Purpose & scope

A repeatable, trackable way to add three entry shapes — **gauge-only**,
**reach-only**, **reach+gauge** — via `dev → migration PR → merge → prod`, without
reintroducing the drift/guard regressions closed across review rounds 2–4.

Initial batches: the **Columbia-River mainstem corridor** (gauge-only) and the
**WA Lewis-system reaches** (reach+gauge), both sourced from data we already have
(the USGS auto-fetch path; the `aw_reach` cache).

## The spine (why every add is two things)

- A **SQL migration** (`data/db/migrations/NNNN_*.sql`) creates the rows on the
  **live** DB when `levels migrate` runs on deploy. Rows link **by name/URL,
  never by hardcoded id** (autoincrement ids are prod-assigned).
- The **nightly metadata snapshot** (`kayak-metadata-snapshot.timer` →
  `scripts/snapshot_metadata.sh` → `export_metadata.py`) dumps prod's metadata
  into `data/db/*.csv` and auto-commits. This is what carries the new rows to a
  **from-scratch rebuild** — `init-db` *stamps* migrations without running them,
  so a fresh DB gets all metadata from the CSVs.
- So a new gauge/reach needs **both**: the migration (for prod) **and** the CSV
  reconciliation (for rebuilds). We never hand-write CSV rows — the snapshot does
  it with the real prod ids.

## Guard checklist (clear all of these, every PR)

| Guard | Fails when | What we do |
|---|---|---|
| **orphan-check** | a fetch-active `source` has no `gauge_source` link | always wire the `gauge_source` join; sandbox-verify |
| **R4.4** (`test_migration_csv_reconciliation`) | a migration-wired source name isn't in `source.csv` | add the source name to `PENDING_RECONCILIATION` in the **same PR**; remove it in a follow-up after the nightly snapshot lands it (the stale-allowlist test forces this) |
| **check-reaches** | `geom` has a `LINESTRING(` wrapper, <2 vertices, out-of-range coords, or endpoints drift >0.003° from the `lat/lon_start/end` columns | the tracer writes correct lon-first, no-wrapper geom; keep the endpoint columns in sync with the geom |
| **dup-prefix** (R5.2) | two migrations share the `NNNN` prefix | next free prefix is **0068** (0067 highest across open PRs — 0065 source-based, 0066 Batch A, 0067 Batch B); re-check open PRs before numbering |
| **model/schema lockstep** | a new column lands without a `models.py` update | N/A here — these batches add no columns |
| **reach HUC** (added after Batch B/C shipped without it) | a new `reach` has NULL or hand-typed `huc` | run `levels assign-huc` on dev → 12-digit `reach.huc` + HUC8-name `reach.basin`, baked into the migration INSERT (Shape 2). A NULL `huc` drops the reach from the watershed filter; verify the new reaches show a 12-digit `huc` |

Plus: **canonical `agency` strings** in migrations (`'USGS'`, `'WA DOE'`,
`'NWRFC'`, `'USBR'`, `'Calculation'` — never the raw parser slug); and the
**universal sandbox check** before every migration PR:

```bash
cp /path/to/prod-or-fresh.db /tmp/sandbox.db
KAYAK_DB=/tmp/sandbox.db .venv/bin/levels migrate
KAYAK_DB=/tmp/sandbox.db .venv/bin/levels orphan-check   # expect: No orphan sources.
KAYAK_DB=/tmp/sandbox.db .venv/bin/levels check-reaches   # if the migration touches reaches
```

---

## Shape 1 — gauge-only

A "gauge" is a `gauge` row + ≥1 `source` (+ `gauge_source` link). The idempotent
idiom (link by name/URL; `0036`/`0063` templates):

```sql
-- fetch source only: fetch_url.url is UNIQUE
INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (…);

-- gauge: name is UNIQUE
INSERT OR IGNORE INTO gauge (name, usgs_id, display_name, latitude, longitude,
    river, state, …) VALUES (…);

-- source: name is NOT unique → guard on name (+ fetch_url_id / agency)
INSERT INTO source (name, agency, fetch_url_id, timezone)
SELECT '<name>', '<canonical agency>', <fu.id or NULL>, '<tz or NULL>'
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '<name>' AND …);

-- link
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '<gauge>' AND s.name = '<source>';
```

**USGS is the easy case (zero extra wiring):** set `gauge.usgs_id`, add a source
`agency='USGS', fetch_url_id=NULL` named the digit station id, link it.
`fetch-usgs-ogc` then auto-fetches params `00060`/`00065`/`00010` (flow / gage /
**temperature**, °C→°F) for any gauge with `usgs_id`. No `fetch_url`, nothing in
`data/sources.yaml`.

**Fetch sources** (WA DOE `_WTM_`, USBR, USACE) additionally need the URL in
`data/sources.yaml` (the pipeline only fetches URLs present there), and USACE
temperature would first need `"Temp-Water": DataType.temperature` added to
`usace_cda.py::_PARAM_MAP` (its own small PR).

**Every wired source name → `PENDING_RECONCILIATION`** in the same PR, removed
after the snapshot.

**State + HUC are required for the browser filter.** `gauges.html` emits a row's
state/watershed filter attributes only when **both** `gauge.state` and `gauge.huc`
(≥8 digits) are set — a gauge missing either is *unfilterable* and shows under
every state. So set `gauge.huc` (the 8-digit HUC8, from the USGS site's `huc_cd`)
**and** `gauge.state` on every gauge-only entry. For a **border gauge** on a
state-line river, set `gauge.state` as a comma list (`OR,WA`): the build splits it
into one `data-state` per state (mirroring how the reach table joins
`reach.states`), and `filters.js` already splits comma `data-state`, so the gauge
filters under each — **no `gauge_state` table needed**. (`gauges.html` reads
state/HUC from the gauge's own columns, not its reaches, so a gauge-only entry
must carry both itself.)

### Reproduce / verify
- Sandbox: `levels migrate` + `levels orphan-check` → "No orphan sources."
- `pytest tests/test_scripts/test_migration_csv_reconciliation.py`
- After prod deploy: `levels fetch-usgs-ogc` (USGS) populates flow/temp.
- Build + confirm the gauge filters under its state(s) in `gauges.html` (needs
  both `state` and `huc8`).

---

## Shape 2 — reach-only

**Source of truth for AW reaches: the `aw_reach` cache**
(`Gauge-metadata-cache/gauges.db`, 2029 runs / 12 states, populated by
`match_aw_reaches.py`). `docs/one-offs/import_mt_reaches.py` is the working
template (pull from cache → insert rows → trace).

Reach data splits across **three paths by size:**

1. **Scalar metadata** → the **migration** (`0039` reach + `0040` links idiom,
   keyed by `aw_id`):
   - `reach`: `name='aw_<id>'`, `display_name`, `sort_name` (keyed by put-in
     `elevation`, high→low ⇒ upstream→downstream — not `aw_id`), the four endpoint
     coords, `river`, `gauge_id` (by gauge name), `description`(=section),
     `difficulties`(=class), `length`, `gradient`, `max_gradient`, `elevation`,
     `elevation_lost`, `aw_id`, plus **`huc` (12-digit HUC12) and `basin` (the
     HUC8 name)** — obtained from `levels assign-huc` (dev-only toolchain below),
     **not** hand-typed: a NULL `huc` drops the reach out of the watershed filter
     (`levels.py` gates the pills on `len(huc) >= 8`) and an 8-digit guess
     diverges from the HUC12 the other ~400 reaches carry. (AW's
     `river`/`display_name`/`description` are inconsistent — normalize them; see
     *Per-reach review* below.)
   - `reach_state` (**required** or it's hidden from state filters).
   - `reach_class` (**required** for the class pills; `name` NOT NULL; CHECK
     `low ≤ high`; set `low`/`high` from AW's runnable range if present).
2. **`geom`** (large, lon-first `"lon lat,lon lat,…"`, no wrapper) → committed to
   `data/db/reaches.json` → applied on prod by `deploy.sh --geom-only`.
3. **`gradient_profile`** (large JSON) → `data/db/reaches-gradient.json` →
   `deploy.sh --gradient-only`. (`max_gradient`, a scalar, stays in the migration.)

**Dev-only toolchain (prod can't):**
- Trace: `levels trace --putin LAT,LON --takeout LAT,LON --name "…"` under
  **brew python** (GDAL/osgeo, not `.venv`), against `Trace-cache/`. Emits the
  no-wrapper geom string. (`import_mt_reaches.py --trace` does this in bulk.)
- Elevation/elevation_lost/gradient: `scripts/refresh_reach_elevations.py
  --reach-ids … --apply` (USGS 3DEP, httpx — dev-only).
- `max_gradient` + `gradient_profile`: the 3-stage `docs/one-offs/` DEM pipeline
  (`fetch_dem_tiles` → `sample_reach_elevations` → `compute_reach_gradient`),
  `DEM-cache/`.
- `huc` + `basin`: `levels assign-huc` (brew python — needs the `[geo]` extra and
  the WBD GPKG in `Trace-cache/`; prod can't run it). Point-in-polygons each
  put-in (`latitude_start`/`longitude_start`) → writes the 12-digit HUC12 to
  `reach.huc` and mirrors the HUC8 name into `reach.basin`; idempotent, so it
  leaves already-correct reaches untouched. Run it once endpoints are final, then
  read the resulting `huc`/`basin` off the dev DB and bake them into the
  migration's reach INSERT. `huc_name` already covers the PNW, so the basin label
  resolves. **Batch B/C (0067/0068) shipped without this step** — 12 reaches
  landed with NULL `huc`, one with an 8-digit guess; backfilled after the fact.

### Reproduce / verify
- `levels check-reaches` (no wrapper, ≥2 vertices, endpoints within 0.003°).
- Render on the dev `reach.php`/`description.php` map; the trace PNG.

---

## Shape 3 — reach+gauge

Shape 1 (gauge) + Shape 2 (reach) — gauge/source first in the migration, then the
reach with `gauge_id` linked by gauge name. Batch B is this shape.

---

## Per-reach review — coords + AW metadata cleanup

The refine loop is iterative — endpoints first on the map, then trace quality,
then names — and the final state matches what's served on the dev
`description.php` before any migration row is written.

1. I **stage** the run on the dev DB from the `aw_reach` cache (AW's raw coords)
   so put-in/take-out markers render on the dev `reach.php`/`description.php`.
2. You **refine endpoints**: **right-click any point on the dev map** —
   `feature-map.js` exposes a contextmenu popup with the cursor lat/lon and a
   Copy button (`map-right-click-latlon` PR). The satellite base map is the
   most accurate for channel placement; topo confirms named landmarks. Send
   the coords; I update the endpoint columns and re-trace.
3. I **re-trace and inspect** on the dev map. NHD HR is "the blue line on USGS
   topo," so a clean trace follows the topo blue line. Common problems + fixes:
   - **Endpoint snapped to wrong flowline** (short straight detour right at
     the put-in / take-out): nudge the endpoint a few metres onto the
     unambiguous main channel; re-trace.
   - **Trace orientation reversed** (a >100 m "jump" between consecutive
     vertices at a splice seam): post-process by reversing the segment
     before the jump.
   - **NHD routes through a side channel** (braided lowland / oxbow areas
     where NHD's "main" path threads through anastomosing channels — EF
     Lewis §5 in Batch B was the canonical case): use the splice in step 4.
4. **Trace splice through main-channel waypoints**: drop waypoints with the
   right-click tool (one every ~100–200 m through the problematic stretch).
   I trace `pi → via1 → ... → take-out` and stitch:
   - For *sparse* gaps (>500 m between adjacent waypoints), `trace_reach`
     fills in along NHD HR — clean for well-behaved stretches.
   - For *dense* waypoints (<200 m apart), I join with a direct polyline;
     `trace_reach` between sub-200 m endpoints can route through a long
     NHD HR detour and inflate the segment length 5–10×.
   - The hybrid (NHD HR on long gaps, polyline on dense groups) is what
     produced reach 417's final geom.
5. **DEM channel-min snap** (canyon-shaped reaches only):
   `docs/one-offs/snap_reach_to_channel_min.py` walks each vertex
   perpendicular to flow, samples the DEM (1 m LIDAR if cached, else 10 m
   3DEP — covers WA/OR/ID), and nudges to the local minimum. Dry-run first;
   the per-reach stats are the gate: **apply** when mean drop ≥ ~10 ft and
   snap rate ≥50% (canyon reaches — Batch B: Lewis NF §1–3, Canyon §1; snap
   43–73%, drop 5–14 ft); **skip** when ≤ ~4 ft / ≤30% (braided lowland — at
   the 10 m DEM's ~8 ft RMSE noise floor; EF Lewis §5 declined). The 1 m
   LIDAR product index has spotty WA Lewis coverage, so all Batch B snaps
   fell through to 10 m.
6. **Compute elevation + gradient_profile** once geoms are final:
   `scripts/refresh_reach_elevations.py --apply` (3DEP EPQS web), then
   `docs/one-offs/sample_reach_elevations.py` (DEM samples along geom) +
   `compute_reach_gradient.py --apply` (max_gradient + gradient_profile).
   `sig_frac ≥75%` per reach indicates real signal. **Reservoir-ending
   reaches** (e.g. Lewis §5 → Swift, Canyon §2 → Merwin) produce a trailing
   non-significant bar for the flat reservoir surface; the
   `svg-plot-keep-reservoir-tail` PR keeps bars ≥0.5 mi wide so the chart
   reads correctly (shorter trailing bars stay suppressed as
   bridge/dam/road DEM artifacts).
7. **Normalize names** — see *Naming and AW cleanup* below.
8. **Final scalar metadata → migration**; **geom → `reaches.json`**;
   **`gradient_profile` → `reaches-gradient.json`**; **`reach_guidebook` rows**
   linking each reach to its canonical state guide (Bennett's WA whitewater
   guide for WA reaches; resolved in the migration by `(title, edition,
   author)` so the row link is portable across DBs).

### Naming and AW cleanup

AW's `river`/`display_name`/`description` vary run-to-run; normalize during the
review pass:

- **`river`**: one canonical value per **branch** so reaches group in the
  table. For sibling forks that share a basin, **share the `river` between
  them** and let `display_name` discriminate (Batch B: `river='Lewis'` for
  both NF and EF; `display_name='NF Lewis'`/`'EF Lewis'`). Tribs with their
  own river name keep it (`river='Canyon Creek'` for the Lewis-trib Canyon
  Creek — clusters in the table with same-named reaches elsewhere, which is
  fine because `sort_name` disambiguates).
- **`description`**: bare location ("Twin Falls to FR 88"), no leading
  section number (`1.`), no trailing parenthetical (`(Upper)`). The leading
  number is implicit in `sort_name`; parentheticals belong in `notes`.
- **`sort_name`**: Sandy-basin convention `<Basin> <letter> NN <section>`.
  Within a basin the letter is the branch (NF=`a`, EF=`b`, sub-tribs=`c`
  onward); NN is the section sequence ordered upstream→downstream by put-in
  `elevation` descending (verify after `refresh_reach_elevations.py`). To
  slot reaches after an existing single-letter sort (`Canyon ad`), use a
  two-letter suffix (`Canyon ae 01`, `Canyon ae 02`).
- The **linked gauge** gets the same `river`/`location`/`display_name`
  cleanup (`seed_gauge_display`'s normalizer is a starting point, not the
  last word). Gauge `sort_name` follows the pipe-delimited convention
  `<river>|<branch-code>|<NNNNNN>|<NNNNNN>` (branch-code `9` for mainstem,
  `0<short>` for sub-rivers — Batch B: `lewis|0north|005000|000000` for
  the new NF Lewis gauge, `lewis|0canyon|005000|000000` for Canyon).
- **`reach_guidebook`**: link each reach to its canonical guidebook with
  `page` + `run`. Skip reaches that aren't indexed (Lewis NF §3 has no
  Bennett entry — fine, no row needed).

`reach` has no `location` column of its own — its geographic "location" is
the put-in/take-out coords from the loop above.

## The id-matching constraint (read before tracing)

`reaches.json` / `reaches-gradient.json` are keyed by `reach.id`, so the
dev-computed ids **must equal** the prod ids. This holds because: the dev DB is
a **fresh prod copy** (refreshed before the migration is generated), our
migration is the **sole** reach-adder in the gap, and we deploy promptly. Don't
let another reach-adding change land on prod between the dev trace and the
deploy.

**Migration generation**: emit reach `INSERT` statements **in `reach.id` order**
from the sandbox so prod's auto-increment lands the same ids. The migration
shouldn't hardcode ids; use `INSERT OR IGNORE INTO reach (...) SELECT ..., g.id,
... FROM gauge g WHERE g.name = '<gauge_name>'` to resolve `gauge_id` by name.
After generating, **verify on a fresh copy of prod**:

```bash
cp ~/tpw/DB/kayak.db /tmp/sandbox_fresh.db
DATABASE_URL=sqlite:////tmp/sandbox_fresh.db levels migrate
DATABASE_URL=sqlite:////tmp/sandbox_fresh.db levels orphan-check   # No orphan sources.
DATABASE_URL=sqlite:////tmp/sandbox_fresh.db levels check-reaches  # 0 with issues
pytest tests/test_scripts/test_migration_csv_reconciliation.py     # 3 passed
DATABASE_URL=sqlite:////tmp/sandbox_fresh.db python scripts/import_metadata.py --geom-only
DATABASE_URL=sqlite:////tmp/sandbox_fresh.db python scripts/import_metadata.py --gradient-only
# Spot-check that new reach.ids match the sandbox ids:
sqlite3 /tmp/sandbox_fresh.db "SELECT id, aw_id FROM reach WHERE aw_id IN (...) ORDER BY id;"
```

---

## Batch A — Columbia mainstem corridor (Shape 1, gauge-only)

Mainstem gauges, river-mile-ordered, grouped by wiring class.

> **Executed (migration `0066`):** the temperature subset only — **4 gauges**:
> Bridgeport, below John Day Dam, The Dalles, and `Bonneville_merge` — a
> 2-source merge of stage `14128870` (tailwater) + Cascade Island temperature
> `453845121564001` on a single gauge (the Wind pattern), enabled by the
> source-based fetcher refactor in companion migration `0065`. Flow/stage-only
> sites (International Boundary, Priest Rapids) and the NWS stage gauges were
> dropped/deferred — temperature was the goal. For John Day Dam the temp
> monitor is the **downstream** WQ site, not the forebay/nav-lock:
> `454249120423500` ("near Cliffs"). All carry `huc8`; the three border gauges
> below McNary set `state='OR,WA'` (multi-state via comma `gauge.state`, no new
> table). The tables below are the original corridor survey, kept for
> reference.

**USGS — zero code** (flow `00060` / gage `00065` / temp `00010` auto-fetched for
any gauge with `usgs_id`; `agency='USGS'`, `fetch_url_id=NULL`, source name = the
station id):

| usgs_id | location | ~RM | data |
|---|---|---|---|
| `12399500` | International Boundary (Northport) | 745 | flow + stage |
| `12438000` | Bridgeport | 544 | temp |
| `12472800` | below Priest Rapids Dam | 397 | flow + stage |
| `454314120413701` | John Day Dam nav lock | 216 | temp |
| `14105700` | The Dalles | 192 | flow + stage + temp |
| `14128870` | Bonneville Dam | 146 | stage + temp |
| `453845121562000` | Bonneville forebay | 146 | temp |

**NWS — fetch source** (`nwps` parser, `agency='NWS'`; needs a `fetch_url` row +
the NWPS URL in `data/sources.yaml`; stage only):

| lid | location | ~RM | data |
|---|---|---|---|
| `VAPW1` | Vancouver | 106 | stage |
| `SHNO3` | St. Helens | 86 | stage |
| `KLMW1` | Kalama | 74 | stage |
| `LOPW1` | Longview | 66 | stage |
| `CBAO3` | Port Westward (Clatskanie) | 53 | stage |
| `WAUO3` | Wauna | 42 | stage |
| `SKAW1` | Skamokawa | 33 | stage |
| `ASTO3` | Tongue Point (Astoria) | 18 | tidal stage |

(McNary Dam, RM 292: USACE-only — deferred; would first need `"Temp-Water"` added
to `usace_cda.py::_PARAM_MAP`, its own small PR.) Every USGS *and* NWS source name
goes into `PENDING_RECONCILIATION` until the snapshot reconciles it.

**Ordering — upstream→downstream.** River miles increase *upstream* (mouth = RM 0),
so upstream→downstream is *descending* RM (Northport 745 → Astoria 18 — the table
order above). Gauges display sorted by `gauge.sort_name`; the default
`seed_gauge_display` key (`10000−elevation`, then drainage) only *approximates*
this and breaks here — the tidal lower river is all ~sea level, and the NWS gauges
carry no elevation/drainage (→ the `999999` sentinel, which sinks them to the end
in arbitrary order). So this batch sets `sort_name` **directly in the migration
from river mile** (`columbia river|9|<NNNNNN>|` with `NNNNNN` a descending-RM key
— high RM sorts first), alongside `river`/`display_name`. Safe because `build`
reads `sort_name` and never recomputes it, and `seed_gauge_display` is a manual
script (don't run it on these gauges — it would clobber the RM order).

## Batch B — WA Lewis system (Shape 3, reach+gauge)

> **Executed (migration `0067`):** **2 new USGS gauges** (NF Lewis `14216000`,
> Canyon Creek `14219000`; EF Lewis `14222500` was already prod gauge 53) +
> **12 reaches** + their `reach_state` / `reach_class` / `reach_guidebook`
> rows. `gradient_profile` for all 12 from the DEM pipeline; geoms refined via
> the right-click loop, with **waypoint splice** on EF Lewis §5 (the lower
> braided river) and **DEM channel-min snap** on the four canyon-shaped
> reaches (Lewis NF §1–3, Canyon §1 — see *Per-reach review* above). 11 of 12
> reaches link to Bennett's WA whitewater guide (NF §3 is the exception, not
> indexed there). Sort keys actually used: `Lewis a 01..05` (NF), `Lewis b
> 01..05` (EF), and `Canyon ae 01..02` (slotting after the existing `Canyon
> ad` on OR reach 179). Per-table CSVs deliberately not updated in this PR —
> same surgical pattern as Batch A; the nightly snapshot PR handles
> `source.csv`/`gauge.csv`/`reach.csv` reconciliation. A follow-up session
> will add 2 more gauges/reaches on the same branch
> (`lewis-reaches`, `import_lewis_reaches.py`'s `REFINED_COORDS` dict already
> carries the post-refine endpoints of the 12 below).

**3 USGS gauges** (Shape 1, same as Batch A) — `14222500` was already in prod
as gauge 53; only `14216000` and `14219000` are new:

| usgs_id | display | serves |
|---|---|---|
| `14216000` | Lewis R above Muddy R near Cougar, WA | NF Lewis runs |
| `14222500` | EF Lewis R near Heisson, WA *(gauge 53, existing)* | EF Lewis runs |
| `14219000` | Canyon Creek near Amboy, WA | Canyon Creek runs |

**12 reaches** (Shape 2, from `aw_reach`), each `gauge_id`-linked:

| Group | AW ids | gauge |
|---|---|---|
| NF Lewis | 3531, 3495, 5711, 2151, 2152 | 14216000 |
| EF Lewis | 2149, 2147, 2150, 2148, 3530 | 14222500 |
| Canyon Creek | 2073, 3066 | 14219000 |

(Rock Creek `2188` set aside — gaugeless; add on request. Other Lewis-basin tribs
under non-"Lewis" names can be pulled from the cache by HUC if wanted.)

The AW-id lists above are just the run inventory — **AW ids and section numbers
are *not* a reliable geographic order.** Order each branch's runs by the **put-in
DEM elevation, high → low (= upstream → downstream)**: after
`refresh_reach_elevations.py` sets `reach.elevation` (the put-in DEM sample), sort
each branch's reaches by `elevation` descending and encode that in
`reach.sort_name` (a `10000−elevation`-style key, like the gauge sort) — **not**
the `aw_id`-based key `import_mt_reaches.py` used. Put-in `elevation` orders any
run with gradient correctly; the lone gap is a **truly flat-water** stretch
(near-equal endpoint elevations) — not foreseen for these branches, but such a run
would need a hand-set `sort_name`.

---

## Execution order (one PR per step, easiest first)

1. **Batch A USGS** (7 Columbia USGS gauges) — exercises the full Shape-1 loop
   end to end: migration + `PENDING_RECONCILIATION` → sandbox → PR → merge → prod
   migrate → nightly snapshot reconciles `source.csv`/`gauge.csv` → follow-up PR
   drops the allowlist entries.
2. **Batch A NWS** (8 lower-river stage gauges) — Shape 1 fetch sources: a second
   migration + the NWPS URLs in `data/sources.yaml`.
3. **Batch B gauges** (3 USGS) — Shape 1, same loop.
4. **Batch B reaches** (12) — Shape 3, executed in migration `0067`. The
   workflow: stage from cache → right-click endpoint refine on the dev map →
   trace + waypoint splice (for braided routes) + DEM channel-min snap (for
   canyons) → elevation + gradient → `assign-huc` (HUC12 + basin) → name +
   AW-metadata normalize + `reach_guidebook` entries → sort each branch by
   put-in elevation → migration
   (metadata + links + guidebooks) + `reaches.json` + `reaches-gradient.json`
   → fresh-prod-copy verify → PR → deploy applies the JSONs.

## Migration numbering

Next free prefix: **0068** (0067 highest committed across the open Batch-A/B
PRs). Re-check open PRs before numbering (R5.2 dup-prefix guard). Keep
`models.py` in lockstep if any column is ever added (not needed for these
batches).
