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
| **dup-prefix** (R5.2) | two migrations share the `NNNN` prefix | next free prefix is **0064** (0063 highest committed); re-check open PRs before numbering |
| **model/schema lockstep** | a new column lands without a `models.py` update | N/A here — these batches add no columns |

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

### Reproduce / verify
- Sandbox: `levels migrate` + `levels orphan-check` → "No orphan sources."
- `pytest tests/test_scripts/test_migration_csv_reconciliation.py`
- After prod deploy: `levels fetch-usgs-ogc` (USGS) populates flow/temp.

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
     `elevation_lost`, `aw_id`. (AW's `river`/`display_name`/`description` are
     inconsistent — normalize them; see *Per-reach review* below.)
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

### Reproduce / verify
- `levels check-reaches` (no wrapper, ≥2 vertices, endpoints within 0.003°).
- Render on the dev `reach.php`/`description.php` map; the trace PNG.

---

## Shape 3 — reach+gauge

Shape 1 (gauge) + Shape 2 (reach) — gauge/source first in the migration, then the
reach with `gauge_id` linked by gauge name. Batch B is this shape.

---

## Per-reach review — coords + AW metadata cleanup

There is **no interactive map editor** (`feature-map.js` is read-only). The
established refine loop, extended for new reaches:

1. I **stage** the run on the dev DB from the `aw_reach` cache (AW's raw coords)
   so its put-in/take-out markers render on the dev `reach.php`/`description.php`.
2. You **refine** via your method — click a marker → Google Maps → visually place
   the true put-in/take-out → send me the lat/lon.
3. I **re-trace** with the refined coords and confirm the line *follows the
   channel* (a bad endpoint or HUC4 mis-detect makes the trace wander) — the
   correctness gate a single marker can't give.
4. Once coords + trace are right: the final coords go into the migration, the geom
   into `reaches.json`.

**AW metadata is inconsistent — normalize it in the same pass.** AW's `river`
(`Lewis, N. Fork` vs `NF Lewis` vs `North Fork Lewis River`…), the run name
(→ `display_name`), and the put-in/take-out `description` vary run-to-run. I
propose canonical values — one river name per branch (so it groups *and* sorts as
a unit), a clean `display_name`, a sensible `description` — and you confirm while
you're looking at the run. The **linked gauge** gets the same
`river`/`location`/`display_name` cleanup (`seed_gauge_display`'s normalizer is a
starting point, not the last word). `reach` has no `location` column of its own —
its geographic "location" is the put-in/take-out coords from the loop above.

## The id-matching constraint (read before tracing)

`reaches.json` / `reaches-gradient.json` are keyed by `reach.id`, so the
dev-computed ids **must equal** the prod ids. This holds because: the dev DB is a
**fresh prod copy** (refreshed 2026-05-27), our migration is the **sole**
reach-adder in the gap, and we deploy promptly. Don't let another reach-adding
change land on prod between the dev trace and the deploy.

---

## Batch A — Columbia mainstem corridor (Shape 1, gauge-only)

Mainstem gauges, river-mile-ordered, grouped by wiring class.

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

**3 USGS gauges** (Shape 1, same as Batch A):

| usgs_id | display | serves |
|---|---|---|
| `14216000` | Lewis R above Muddy R near Cougar, WA | NF Lewis runs |
| `14222500` | EF Lewis R near Heisson, WA | EF Lewis runs |
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
4. **Batch B reaches** (12) — Shape 3: stage from cache → your coord + metadata
   review → trace + elevation + gradient on dev → sort each branch by put-in
   elevation → migration (metadata + links) + `reaches.json` +
   `reaches-gradient.json` → PR → deploy applies the JSONs.

## Migration numbering

Next free prefix: **0064** (0063 highest committed). Re-check open PRs before
numbering (R5.2 dup-prefix guard). Keep `models.py` in lockstep if any column is
ever added (not needed for these batches).
