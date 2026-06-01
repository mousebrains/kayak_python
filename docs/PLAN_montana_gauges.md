# Plan — Montana USGS gauges (curated list, 13 sites)

**Status:** Revised 2026-05-19 (third revision). Two scope changes from
the previous draft:

1. Original site scope (HUC4 1701, 62 auto-discovered sites) narrowed to
   a hand-picked list of 13 USGS gauges from
   [`docs/one-offs/mt.list`](../docs/one-offs/mt.list).
2. The Phase-3 `gauges.<state>.html` builder (already merged for OR/WA/ID
   on this branch) is being **reverted** in favor of the existing
   fragment-filter mechanism (`filters.js` honors `#st=<state>` on
   `gauges.html` and `index.html`; `gauge_picker.php` honors
   `?state=<full-name>`; `picker.php` gets a matching HTML-entry parser
   as part of this PR). State landing pages (`Oregon.html`, the new
   `Montana.html`, etc.) become the canonical entry points to filtered
   views.

Phase 1 (discovery-script edit) stays merged. Phase 2 migration
`0036_montana_usgs_gauges.sql` needs **regeneration** from mt.list
before merge. Not yet applied to any DB.

## Goal

Add 13 hand-picked USGS continuous gauges across Montana — spanning both
the Pacific drainage (HUC4 1701) and the Missouri drainage (HUC4 1002/1003) —
to the database, fetched by the existing `fetch-usgs-ogc` pipeline, and
surface them via:

- `gauges.html#st=Montana` — filtered view of the all-states gauges
  page; Montana auto-appears as a State pill once Phase 2 lands (the
  pill list is data-driven from the row set).
- `Montana.html` — new state landing page with cross-links to the
  filtered `gauges.html`, the filtered `index.html` (when reaches
  exist — out of scope this PR), and the `gauge_picker.php` /
  `picker.php` pages with the Montana state pre-checked via the
  existing query-param machinery.

No `gauges.montana.html`. No state-scoped page builder.

Reaches remain **out of scope** for this pass. AW reaches that pair with
these gauges — both cache-recorded matches and Pat-curated proxies —
are documented in § AW reach associations below as input for a
follow-up reach-import PR.

## Curated list

[`docs/one-offs/mt.list`](../docs/one-offs/mt.list) — transcribed by Pat 2026-05-19
from the entries circled on
<https://levels-legacy.wkcc.org/?P=Montana.html>. Two-column TSV (third
column is a human-readable label, ignored by tooling):

```
<row#>\t<usgs_site_no>\t<label>
```

Recap of the 13 sites (metadata from `Gauge-metadata-cache/gauges.db::usgs_site`):

| # | USGS ID | Station name | HUC8 | Basin | Last flow obs |
|---|---|---|---|---|---|
| 1 | 06090500 | Belt Creek near Monarch MT | 10030105 | Missouri | 2026-05-19 |
| 2 | 06025500 | Big Hole River near Melrose MT | 10020004 | Missouri | 2026-05-19 |
| 3 | 06025250 | Big Hole River at Maiden Rock nr Divide | 10020004 | Missouri | 2026-05-19 |
| 4 | 12340000 | Blackfoot River near Bonner MT | 17010203 | Columbia | 2026-05-19 |
| 5 | 12354500 | Clark Fork at St. Regis MT | 17010204 | Columbia | 2026-05-19 |
| 6 | 06073500 | Dearborn River near Craig MT | 10030102 | Missouri | 2026-05-19 |
| 7 | 12359800 | S F Flathead R ab Twin C nr Hungry Horse | 17010209 | Columbia | 2026-05-19 |
| 8 | 06036650 | Jefferson River near Three Forks MT | 10020005 | Missouri | 2026-05-19 |
| 9 | 06038800 | Madison River at Kirby Ranch nr Cameron | 10020007 | Missouri | 2026-05-19 |
| 10 | 06066500 | Missouri River bl Holter Dam nr Wolf Cr | 10030102 | Missouri | 2026-05-19 |
| 11 | 06077200 | Smith River bl Eagle Cr nr Fort Logan MT | 10030103 | Missouri | 2026-05-19 |
| 12 | 06077500 | Smith River near Eden MT | 10030103 | Missouri | 2026-05-19 (temp dormant since 2017-07) |
| 13 | 06085800 | Sun River at Simms MT | 10030104 | Missouri | 2026-05-19 |

10/13 are east of the Continental Divide. The HUC4 1701 boundary that
justified the original discovery sweep is **no longer the scope** —
mt.list is the source of truth.

## Scope decisions (revised)

| Decision | Choice | Rationale |
|---|---|---|
| Site source | **Curated list — `docs/one-offs/mt.list` (13 sites)** | Replaces the HUC4 discovery sweep. Pat picked these from the legacy site's Montana page. |
| Geographic boundary | **None** | The list spans Columbia + Missouri drainages. Boundary was a discovery aid, not a product requirement. |
| Source mix | **USGS continuous only** (NWIS OGC: params 00060, 00065, 00010) | Same as before — auto-discovered via `gauge.usgs_id`. Zero `sources.yaml` change. |
| Active cutoff | **Implicit** — manual curation already filtered for active sites | All 13 sites have flow obs within the last 24 h per the cache. |
| Output URL | `gauges.html#st=Montana` (filtered) + new `Montana.html` landing page | Replaces the planned `gauges.montana.html`. The fragment filter is already wired (`filters.js`); the landing page hosts cross-links to filtered views + curated external resources, matching the existing Oregon/Washington/Idaho/Nevada/California pattern. |
| Reaches | **Out of scope** | Same as before. AW IDs documented for follow-up. |

## Commits on this branch + disposition

| Commit | Disposition |
|---|---|
| `ce6f2d3` docs: plan Montana USGS gauge harvest + gauges.montana.html page | superseded by this revision |
| `0d1d4b1` scripts: add Montana to USGS site discovery, skip lon filter for MT | **keep** — cache covers MT; useful for future expansions |
| `fff0bf8` build: state-scoped gauges page (gauges.<state>.html) | **revert** — see Phase 3 below |
| `2e18e14` data/db: migrate 0036 — Montana USGS gauges (HUC4 1701, 62 sites) | **rewrite** — see Phase 2 below |
| `db82e3b` style: ruff format the Phase 3 changes | absorbed in the revert (ruff will reformat what's left) |
| `7a42220` build: state-scoped gauges pages for OR / WA / ID | **revert** — Phase 3 |
| `17f9efe` refactor: extract _state_slug, single source of truth for filename rule | **revert** — `_state_slug` has four call sites, all tied to `gauges.<state>.html` generation (`deploy.py:429`, `shell.py:334`, `gauges.py:553`, plus the `_shared.py:60` definition + imports). All four go away with Phase 3. State landing pages use `f"{state}.html"` (preserves capitalization — `Montana.html`, not `montana.html`), so `_state_slug` finds no consumer post-revert. |
| `df6581e` picker: pre-initialize state filter from gauges.<state>.html | **rework** — `gauge_picker.php?state=<full-name>` parser stays intact; the trigger moves from "arriving from gauges.<state>.html nav-bar" (now gone) to "arriving from `<State>.html` landing-page link". This PR also adds the matching `?state=<full-name>` parser to `picker.php` (which didn't have one — the existing `?states=<comma>` on line 22 is AJAX-only). |

`0036` has not been applied to any DB (the local Mac dev DB shows 0035 as
the latest applied row in `schema_migrations`). Safe to **rewrite in
place** on this feature branch rather than chaining a 0037 to delete +
re-insert.

The Phase-3 reverts are also safe to do in-branch by adding a new
commit that undoes them — the feature branch hasn't shipped to
production. The single dependency to keep an eye on is any
`gauges.<state>.html` already generated in a local `public_html/`
checkout: those will linger until the next clean deploy or a manual
`rm`. None are tracked in git.

## What's left to do

### Phase 2 (revised): Regenerate `0036_montana_usgs_gauges.sql`

#### Generator change

`docs/one-offs/generate_mt_migration.py` currently reads
`data/discover/montana_candidates.csv` (the gitignored discovery output).
Switch it to read `docs/one-offs/mt.list` directly and pull metadata from
`Gauge-metadata-cache/gauges.db::usgs_site`:

- Parse mt.list: skip blank lines, take column 2 (USGS site number) of
  each remaining row. The third column (label) is a human readability
  aid — ignored by the generator. The first column (row index) is also
  ignored.
- For each site_no, `SELECT station_nm, latitude, longitude, huc_cd,
  drain_area_sq_mi, altitude_ft FROM usgs_site WHERE site_no = ?` on
  the gauges.db cache. Error out if any site_no is missing (curated
  list should match the cache; missing rows mean the cache is stale —
  re-run `scripts/fetch_usgs_sites.py`).
- Emit SQL in the order they appear in mt.list (stable diff if the
  list is reordered).
- Drop the `_REVIEW_HINTS` heuristic and "REVIEW:" comments — the
  curated list has already been hand-screened, so flagging is noise.
- Update the migration's leading comment block from "HUC4 1701 …
  candidate CSV" to "curated list — `docs/one-offs/mt.list`".

Per-site SQL pattern unchanged — three idempotent inserts (`source`,
`gauge`, `gauge_source`). Field-derivation rules unchanged (see § Field
derivation below).

#### Field derivation (unchanged from earlier revisions)

- `name` / `station_id` / `usgs_id`: `{site_no}` — editors fill in human-readable names later via the UI.
- `display_name`: parsed from station_nm using the existing
  `parse_station_name` helper (handles `bl`/`nr`/`ab`/`abv` expansion,
  strips trailing `, MT`). Falls back to raw station_nm when no
  position word is found.
- `river` / `location`: same parser as `display_name`. Curated list
  parses cleanly for all 13 sites (verified — no `zz_unparsed` rows).
- `sort_name`: `"<basin>|9|<10000−elev_ft, 6-digit zero-padded>|<drain_area_sq_mi, 6-digit zero-padded>"`. Drives A–Z letter nav on the state page (`gauges.py:381`). Basin = lowercase-with-spaces-stripped river name.
- `huc`: store whatever USGS returns (typically 12-digit; one site has 8-digit).
- `allow_negative_flow`: 0 (no tidal sites in MT).
- **No `;` in any string field** (defensive — `cli/migrate.py::_split_statements` splits on `;` without parsing string literals).

#### Reproduce

All `levels …` commands assume the project venv is on PATH (Mac
dev box: `~/.venv/bin/levels` or wherever Pat keeps it; Linux dev/prod:
`/home/pat/.venv/bin/levels`). Adjust the `KDB` variable for whichever
DB you're verifying against — `/Users/pat/tpw/DB/kayak.db` on Mac,
`/home/pat/DB/kayak.db` on Linux.

```bash
KDB=/Users/pat/tpw/DB/kayak.db   # or /home/pat/DB/kayak.db

# Confirm the cache has all 13 sites
sqlite3 Gauge-metadata-cache/gauges.db \
  "SELECT COUNT(*) FROM usgs_site WHERE site_no IN
   ('06090500','06025500','06025250','12340000','12354500',
    '06073500','12359800','06036650','06038800','06066500',
    '06077200','06077500','06085800');"
# Expected: 13. If less, refresh: python3 scripts/fetch_usgs_sites.py

# Regenerate the migration from mt.list
python3 docs/one-offs/generate_mt_migration.py

# Sanity: 13 of each insert
grep -c "INSERT INTO source"               data/db/migrations/0036_montana_usgs_gauges.sql  # 13
grep -c "INSERT OR IGNORE INTO gauge "     data/db/migrations/0036_montana_usgs_gauges.sql  # 13
grep -c "INSERT OR IGNORE INTO gauge_source" data/db/migrations/0036_montana_usgs_gauges.sql # 13

# Collision check vs the live DB (none of the 13 should already exist)
sqlite3 -header "$KDB" <<SQL
SELECT g.id, g.name, g.state FROM gauge g
WHERE g.name IN ('06090500','06025500','06025250','12340000','12354500',
                 '06073500','12359800','06036650','06038800','06066500',
                 '06077200','06077500','06085800');
SQL
# Expected: no rows.

# Dry run on a sandbox DB
cp "$KDB" /tmp/kayak-sandbox.db
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db levels migrate

# Confirm 13 MT gauges + 13 gauge_source links
sqlite3 /tmp/kayak-sandbox.db <<'SQL'
SELECT COUNT(*) AS mt_gauges FROM gauge WHERE state = 'MT';   -- 13
SELECT COUNT(*) AS mt_linked FROM gauge g
  JOIN gauge_source gs ON gs.gauge_id = g.id
  JOIN source s ON s.id = gs.source_id
  WHERE g.state = 'MT' AND s.agency = 'USGS';                  -- 13
SELECT id, name, river, display_name, huc FROM gauge WHERE state = 'MT' ORDER BY sort_name;
SQL

# Confirm fetch-usgs-ogc picks up the new sites
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db levels fetch-usgs-ogc \
    --dry-run --hours 12 2>&1 | tail -20
```

**Re-run safety:** `schema_migrations` records completion; if you need
to re-verify on the same sandbox, delete the row first (`DELETE FROM
schema_migrations WHERE version='0036';`). The migration's `INSERT OR
IGNORE` / `WHERE NOT EXISTS` guards keep the SQL re-executable; only
the bookkeeping row needs the manual reset.

`levels orphan-check` is intentionally not part of verification — these
sources have `fetch_url_id = NULL`, which the orphan-check filter
excludes (`src/kayak/db/sources.py:105`).

### Phase 1 retirement

The Phase 1 discovery work (extending `fetch_usgs_sites.py` to cover MT)
stays merged. The cache it builds remains useful: the Phase 2 generator
reads it for metadata, and a future "extend MT coverage" PR can pull
from the same cache without re-running the USGS site service. The
`data/discover/montana_candidates.csv` artifact is now superseded by
mt.list and can be deleted from local working trees (it's gitignored —
nothing to commit).

### Phase 3 (revised): Revert `gauges.<state>.html`, route state views through fragment filter + landing pages

#### Why this changes

The Phase-3 commits on this branch generated `gauges.<state>.html` as
state-scoped duplicates of `gauges.html`. The same view is already
available via `gauges.html#st=<state>` — `filters.js` reads the
`#st=` fragment, the State filter pill is data-driven from the
visible rows, and Montana's pill auto-appears once Phase 2 lands.
The same fragment pattern works on `index.html`. The pickers
already support pre-filtering: `gauge_picker.php?state=<full-name>`
is wired (per `df6581e`), and `picker.php` gets a matching
HTML-entry parser as part of this PR (small PHP edit; see § Picker
pre-fill). Generating a separate state-scoped page is redundant.

The cleaner architecture: a state landing page (`Oregon.html`, the
new `Montana.html`, etc.) owns the cross-links into filtered views.
Users arriving at `Oregon.html` see top-of-page anchors for
"Oregon Reaches", "Oregon Gauges", "Reach picker — Oregon", and
"Gauge picker — Oregon" — all pre-filtered to OR. Montana, gauges-
only this PR, gets the two gauge-flavored anchors (Gauges + Gauge
picker) and the reach anchors are suppressed until reaches land.
No new HTML artifacts per state; all filtering happens on existing
pages via existing JS/PHP.

#### Code changes

| File | Change |
|---|---|
| `src/kayak/web/build/deploy.py` | Drop the `_state_slug` import (line 38) and the `for abbrev in ("MT", "OR", "WA", "ID"):` loop at lines 422–430 along with the `extra_sitemap_urls` accumulator. `_emit_sitemap` reverts to no `extra_urls` kwarg (or kwarg kept but always-None caller). **Also**: change the landing-page loop (lines 436–441) from `for state in _NAV_STATES: if state in states:` to plain `for state in sorted(_NAV_STATES):` — drop the reach-presence guard so Montana (gauges only) still gets `Montana.html`. **Also**: change the sitemap loop at line 475 from `for state in states:` to `for state in sorted(_NAV_STATES):` so Montana.html ends up in `sitemap.xml`. |
| `src/kayak/web/build/gauges.py` | Remove the `state: str \| None = None` kwarg on `_write_gauges_page` and the state-filter branch in its body (~lines 543–570). Function returns to its pre-Phase-3 signature. Drop the `_state_slug` import (line 23) — no remaining consumers in this module. The `_build_gauges_filter_bar(..., is_all_page=True)` call at line 572 also reverts to no `is_all_page` kwarg (becomes a positional default). |
| `src/kayak/web/build/shell.py` | Drop the `_state_slug` import (line 17). `_build_placeholder_page` rewritten: drop the `gauge_state_pages` param and the "→ Live {state} gauge readings" anchor. Replace with an unconditional 3- or 4-anchor block near the top: `[Reaches in {state}]`, `[Live {state} gauges]`, `[Reach picker — {state}]`, `[Gauge picker — {state}]`. Conditionally suppress the reach anchor + reach-picker anchor when the state has zero reaches (Montana for this PR — gate on `state in states` since `states` is the reach-states list from `all_state_names()`). URL-encode multi-word state names via `urllib.parse.quote` (e.g. `New%20Mexico`) — pattern already in use in `df6581e`. The "Gauges" link on the nav-bar (line 331-336) also reverts to the unconditional `/gauges.html` — no state-scoped variant exists to pre-fill against. **Also**: `_build_nav` (line 157) currently iterates `for s in states: if s not in _NAV_STATES: continue` — switch to `for s in sorted(_NAV_STATES):` (drop the reach-presence filter) so the header nav shows Montana even though MT has no reaches yet. **Also**: nav-bar Reach Picker link (line 173, in the `else` branch) — add `?state={_urlquote(active_state)}` when `active_state` is set, mirroring the gauge-picker logic at lines 168–170. The `picker.php` parser added in this PR (see file row below) makes this pre-fill effective. Update the picker-pre-fill docstring comment (lines 164–167) to drop the "arriving from gauges.<state>.html" wording — the trigger source is now landing pages. |
| `src/kayak/web/build/_shared.py` | Add `"Montana"` to `_NAV_STATES` (line 57). Header nav (CA/ID/NV/OR/WA + new MT) is built from this set. **Delete** the `_state_slug` helper (lines 60–68) — no remaining consumers post-revert. `_STATE_ABBREVS` already contains `"Montana": "MT"` (line 46), so the nav-bar abbrev rendering works with zero further change. |
| `src/kayak/web/build/shell.py` | Add `"Montana"` entry to `_STATE_LINKS`. Initial list paralleling Oregon/Washington/Idaho — concrete URLs TBD; placeholder set: American Whitewater MT, Dreamflows (Pacific NW), USGS Montana water data, NWRFC, USBR Hydromet, Montana Whitewater Association (if applicable), Montana Weather → Windy. Pat to confirm the list before commit. |
| `php/picker.php` | **add `?state=<full-name>` parser** mirroring the `gauge_picker.php` pattern from `df6581e`. Currently `$primary_state = 'Oregon';` is hardcoded at line 113. Add an override after `$all_states` is built (~line 121): if `?state=` is present and the name is in `$all_states`, use it as `$primary_state`. ~10 lines. Pre-fill now works on both pickers — the user's `<State>.html` landing-page anchor lands in a state-focused view either way. |
| `tests/test_build_gauges_state_filter.py` | **Delete** — pinned the reverted Phase-3 builder. |
| `tests/test_build_filters.py` | Keep `is_all_page=False` tests if any state-scoped *reach* page still uses the flag; otherwise the flag becomes unused and the test can be retired in a follow-up. |
| `tests/test_placeholder_state_links.py` (new) | For each `_NAV_STATES` entry, assert the generated `<State>.html` contains `href="/gauges.html#st=<State>"`, the picker anchors with the correct `?states=` / `?state=` query string, and (for states with reaches) the reaches anchor. One parametrized test that catches a missing-state-link regression. |
| `public_html/gauges.oregon.html`, `gauges.washington.html`, `gauges.idaho.html` | Untracked — leftover from the Phase-3 builder run. Manual `rm` after the revert, or rely on the next clean deploy (deploy job's `find … -delete` pre-step, if it has one — check `deploy/` scripts). |

#### Picker pre-fill

Two pickers; their pre-fill parsers diverge today and we're normalizing
them in this PR:

- **`gauge_picker.php`** — already has `?state=<full-name>` pre-fill on
  the HTML entry (added in `df6581e`, lines 144–148). Stays intact.
- **`picker.php`** — only has `?states=<comma>` on the **AJAX** endpoint
  (line 22). The HTML entry hardcodes `$primary_state = 'Oregon'`
  (line 113), so a landing-page link to `picker.php?state=Montana`
  wouldn't actually pre-check Montana — Oregon would still be the
  primary. **Add** the `?state=<full-name>` HTML-entry parser
  mirroring `gauge_picker.php`'s implementation (see the file row
  above).

After the picker.php edit, both pickers accept `?state=<full-name>` on
the HTML entry. Landing-page anchors carry that param, so arriving at
a picker from `Montana.html` (or any `<State>.html`) lands the user
in a state-focused view — the same UX that arriving from
`gauges.<state>.html` used to give before Phase 3 was reverted.

The `?states=<comma>` AJAX param is unchanged on both files; client-
side filter scripts continue to use it for multi-state queries.

#### Test coverage

| Test | What it pins |
|---|---|
| `tests/test_placeholder_state_links.py::test_landing_page_has_filter_anchors` (new) | Each `<State>.html` contains the three (or four) cross-link anchors with correctly URL-encoded `#st=` and `?state=` / `?states=` query strings. Catches a missing anchor or a typo in the encoder. |
| `tests/test_placeholder_state_links.py::test_montana_landing_omits_reach_anchors` (new) | Montana (no reaches) has the gauges + gauge-picker anchors but not the reaches + reach-picker anchors. |
| `tests/test_placeholder_state_links.py::test_nav_bar_reach_picker_carries_active_state` (new) | A landing page's nav-bar Reach Picker link carries `?state=<active_state>` once `_build_nav`'s symmetric pre-fill lands. |
| `tests/php/test_picker_state_param.php` (new) | PHP integration test: GET `picker.php?state=Oregon` — assert the `Oregon` checkbox is rendered with `checked`, other state pills are not. Seed via `seedDatabase()` per the existing `IntegrationTestCase.php` pattern. Mirrors any existing test for `gauge_picker.php?state=`. |
| `tests/test_build_filters.py::test_build_filter_bar_omits_state_on_single_state_page` | Currently exercises the `is_all_page=False` path. With Phase 3 reverted there's no state-scoped *gauges* page; the `is_all_page` flag survives only for state-scoped reach pages if those still exist. If not, the test gets retired in the revert commit. |
| Existing `tests/test_build_gauges_state_filter.py` | **Removed** alongside the builder. |

No tests needed for the migration regeneration — the sanity counts in §
Phase 2 Reproduce verify that.

## AW reach associations

Two flavors of association are tracked. The reach-import follow-up PR
will land both, but proxy rows need MT-boater sign-off before they
ship to paddlers.

1. **Cache-recorded** — `Gauge-metadata-cache/gauges.db::aw_reach.gauges`
   JSON references our USGS site number directly. AW considers this
   gauge the canonical reading for the reach.
2. **Paddler-curated proxy (pending MT-boater verification)** — AW's
   canonical gauge for the reach is *not* in our 13-site list (either
   AW lists no gauge at all, or AW lists a gauge we deliberately
   skipped per the curated-list scope). Pat has nominated a nearby
   in-list gauge as a usable proxy and will confirm with Montana
   boaters before the reach-import lands. The AW-canonical gauges are
   intentionally **not** being added to mt.list.

### Cache-recorded (11 reaches across 6 gauges)

| USGS ID | Station | AW reach IDs | Reaches |
|---|---|---|---|
| 06090500 | Belt Creek nr Monarch | 981 | "2. Monarch to Riceville" (II–III, 16 mi, 250–1000 cfs) |
| 12340000 | Blackfoot nr Bonner | 984 | "Scotty Brown Bridge → Johnsrud Park" (II–III) |
| 12354500 | Clark Fork at St. Regis | 995, 996 | "Cyr to Tarkio" (III–IV); "Tarkio to Forest Grove" (II) |
| 06073500 | Dearborn nr Craig | 998, 4358, 4359 | Dearborn upper canyon (III–V+); Dearborn Rd → Hwy 287 (I–II); Falls Creek tributary (III–V+) |
| 12359800 | SF Flathead ab Twin C | 1005, 3778, 10916 | "Youngs Creek → Mid Creek" (II+); "Cedar Flats → Upper Twin Creek" (II); Upper Twin Creek hike-in (III+ to V+) |
| 06077200 | Smith bl Eagle Cr | 1021 | "Camp Baker to Eden Bridge" (I–II) |

### Paddler-curated proxy — pending MT-boater verification (5 reaches across 4 gauges)

| USGS ID | Proxy station | AW reach | AW canonical gauge | Geographic note |
|---|---|---|---|---|
| 06025250 | Big Hole at Maiden Rock | 983 — Big Hole "Dewey to Divide Bridge" (II/III, 53.4 mi, level-ft 2.0–7.0) | 06026210 Big Hole nr Glen | Maiden Rock is **at Divide** (right at the reach takeout). Both AW's canonical Glen gauge **and** our other Big Hole gauge (Melrose) sit downstream of Divide; Maiden Rock is the closest in-list reading to this reach by far. |
| 06038800 | Madison at Kirby Ranch | 1012 — Madison "3) Beartrap Canyon: Madison Dam → Route 84" (I–IV, 12 mi, 600–4000 cfs) | 06041000 Madison bl Ennis Lake nr McAllister | Kirby Ranch is upstream of Ennis Lake; Madison Dam (the reach put-in) is the Ennis Lake outlet. Kirby tracks lake *inflow*. |
| 06038800 | Madison at Kirby Ranch | 1013 — Madison "2) Quake Lake → 1.5 mi downstream" (IV–V, 1.5 mi, 700–2700 cfs) | 06038500 Madison bl Hebgen Lake | Kirby Ranch is downstream of Quake Lake. Hebgen-outflow gauge is AW canonical; Kirby is the next mainstem gauge below Quake. |
| 06066500 | Missouri blw Holter Dam | 3227 — Missouri "Great Falls" (II/V, 5.8 mi, no AW runnable range) | — (AW lists no gauge) | Holter Dam is upstream of Great Falls; the Dearborn, Smith, and Sun join the Missouri between Holter and Great Falls (and the Black Eagle / Rainbow / Cochrane / Ryan / Morony dam chain is in between). Holter reads dam outflow. |
| 06085800 | Sun at Simms | 10730 — Sun River **South Fork** "Wilderness Run" (II–III/V, 12.4 mi, no AW runnable range) | — (AW lists no gauge) | Simms is on the mainstem Sun River, downstream of the North/South Fork confluence. The SF Wilderness reach drains a fraction of the basin Simms reads. |

### Coverage summary

| | Gauges | AW reaches |
|---|---|---|
| Cache-recorded | 6 | 11 |
| Paddler-curated proxy | 4 | 5 |
| **Total** | **10 of 13** | **16 distinct AW IDs** |

Three gauges in mt.list have no AW association of either flavor:
06025500 (Big Hole nr Melrose), 06036650 (Jefferson nr Three Forks),
06077500 (Smith nr Eden). They stand as raw flow monitors only.

The `reach` table has an `aw_id INTEGER` column ready for traceability.
For proxy associations the follow-up reach-import will need to record
that our gauge is not the AW-canonical one — simplest carrier is a
short explanatory line in `reach.notes` so paddlers know they're
reading a proxy, not the AW-recommended gauge.

## Load estimate

13 sites instead of 62 — current DB has 150 USGS gauges, so the total
goes 150 → 163. Still in the 2-batch range for `fetch-usgs-ogc`
(150-site batch limit, ~3 base calls per batch per param) — the same
"+3 base calls/hour" estimate from the earlier revisions applies, and is
effectively unchanged.

Audit noise on first run is now 13 newly-data-providing gauges instead
of 62 (still trips `scripts/audit_gauges.py`'s "started providing data
in the last week" detector once per run). Mention in the PR description.

## Files touched (final list, revised)

### Phase 2 (data)

| Path | Change |
|---|---|
| `docs/one-offs/mt.list` | new — curated 13-site list, source of truth |
| `docs/one-offs/generate_mt_migration.py` | **edit** — read `docs/one-offs/mt.list` + `Gauge-metadata-cache/gauges.db` instead of `data/discover/montana_candidates.csv`; drop REVIEW-hint heuristic; refresh leading comment |
| `data/db/migrations/0036_montana_usgs_gauges.sql` | **rewrite** — regenerate from mt.list (13 sites; was 62 HUC4-1701 sites) |

### Phase 3 (revert state-scoped pages + landing-page cross-links)

| Path | Change |
|---|---|
| `src/kayak/web/build/deploy.py` | drop the state-scoped gauges-page loop + `extra_sitemap_urls`; switch landing-page loop + sitemap loop from `states` (reach-presence) to `sorted(_NAV_STATES)` |
| `src/kayak/web/build/gauges.py` | remove `state` kwarg + filter branch on `_write_gauges_page`; drop `_state_slug` import |
| `src/kayak/web/build/_shared.py` | add `"Montana"` to `_NAV_STATES`; **delete** `_state_slug` helper (no remaining consumers) |
| `src/kayak/web/build/shell.py` | rewrite `_build_placeholder_page` (cross-link anchors); add `_STATE_LINKS["Montana"]`; revert nav-bar gauges link to unconditional `/gauges.html`; switch `_build_nav` state iteration to `sorted(_NAV_STATES)`; **add `?state=<active_state>` to the nav-bar Reach Picker link** when `active_state` is set, mirroring the existing gauge-picker case at lines 168–170 (symmetric pre-fill now that `picker.php` supports the parser) |
| `php/picker.php` | new — add HTML-entry `?state=<full-name>` parser mirroring `gauge_picker.php` |
| `tests/test_build_gauges_state_filter.py` | **delete** |
| `tests/test_placeholder_state_links.py` | new — pins the cross-link anchors on every `<State>.html` + nav-bar Reach Picker pre-fill |
| `tests/php/test_picker_state_param.php` | new — PHP integration test for `picker.php?state=<full-name>` |

### Docs

| Path | Change |
|---|---|
| `docs/PLAN_montana_gauges.md` | this revision |

### Local cleanup (not committed)

| Path | Action |
|---|---|
| `public_html/gauges.oregon.html`, `public_html/gauges.washington.html`, `public_html/gauges.idaho.html` | `rm` after the revert lands. Not tracked in git; will not be regenerated. |
| `data/discover/montana_candidates.csv` | `rm` — superseded by `docs/one-offs/mt.list`. Gitignored. |

Untouched by this revision (kept from earlier commits):
`scripts/fetch_usgs_sites.py` (Montana coverage stays merged),
`src/kayak/web/static/filters.js` (already handles `#st=` correctly),
`php/gauge_picker.php` (its `?state=` parser stays intact —
`picker.php` is the only one needing the new parser).

## Follow-ups (deliberately not in this PR)

- **Import the 16 AW reaches above** for the 10 gauges that have them.
  Adds `reach` rows (with `aw_id` populated for traceability),
  `reach_state` MT links, and `reach_class` rows. Montana is already
  in `_NAV_STATES` (added in this PR), so the reaches surface in the
  nav and the existing `Montana.html` landing page automatically
  un-hides its "Reaches in Montana" + "Reach picker — Montana"
  cross-links once any `reach_state` row links a reach to MT (the
  landing-page suppression rule keys on reach presence; see
  `_build_placeholder_page`). All metadata (river, section, class,
  putin/takeout coords, length, runnable flow range, flow_metric) is
  already in `aw_reach`. Split into two PRs / commits:
   1. **Cache-recorded matches (11 reaches, 6 gauges)** — straightforward;
      `reach.gauge_id` points to the cache-listed gauge.
   2. **Proxy matches (5 reaches, 4 gauges)** — blocked on MT-boater
      sign-off (Pat will confirm). Once confirmed, `reach.gauge_id`
      points to the proxy gauge from mt.list, and `reach.notes`
      records the divergence — e.g. *"Flow shown is from USGS
      06038800 (Kirby Ranch) — upstream of Ennis Lake. AW's canonical
      gauge for this reach is 06041000 (below Ennis Lake)."* — so
      paddlers know they're reading a proxy. If the boaters reject a
      proxy, drop the reach from the import (or revisit the
      curated-list scope to add the AW-canonical gauge).
  Bigger lift than the gauges-only pass either way — separate PR.
- **NWPS observed for MT forecast points.** Layer NWPS URLs onto any of
  the 13 USGS sites that pair with a NWPS LID for outage redundancy.
  1 HTTP call per LID, trivial.
- **NWRFC textPlot / XML.** Only worth it for stations where USGS drops
  a parameter and NWRFC computes flow via its local rating curve.
  Defer until a specific need appears.
- **Belt Creek water-temp gap.** `06090500.last_temp_date` is NULL —
  USGS doesn't publish temp here. Display layer already handles
  missing-param gracefully; verify after first build.
- **Smith River nr Eden temp dormant since 2017.** Same handling —
  flow + stage will show, temp column will be blank.
- **Extending the MT list.** If/when Pat circles more entries, append
  to mt.list and re-run the generator; the migration is keyed on
  site_no with `INSERT OR IGNORE`, so re-applying after appending is
  idempotent. A new migration file (0037+) is only needed if existing
  sites' field values change.
