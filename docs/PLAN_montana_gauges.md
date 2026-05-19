# Plan — Montana USGS gauges (HUC4 1701, west of Continental Divide)

**Status:** Drafted 2026-05-19. Iterated through five self-review passes; no new findings on the last pass. Not yet implemented.

## Goal

Add USGS continuous gauges in the Columbia drainage portion of Montana
(HUC4 1701 ∩ state=MT) to the database, fetched by the existing
`fetch-usgs-ogc` pipeline, and surface them on a new state-scoped
`gauges.montana.html` page. Reaches are explicitly **out of scope** for
this pass; we want the raw gauge data first.

## Scope decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Geographic boundary | **HUC4 1701 ∩ state=MT** | Matches NWRFC's Pacific drainage footprint cleanly; sidesteps the messy "what does west-of-Helena mean" question. Covers Kootenai (170101), Pend Oreille / Clark Fork / Flathead / Bitterroot / Blackfoot (170102), and a thin slice of Spokane HUC6 (170103) in MT's far-NW corner where the Pend Oreille leaves the state. |
| Source mix | **USGS continuous only** (NWIS OGC: params 00060, 00065, 00010) | Already auto-discovered via `gauge.usgs_id`; zero `sources.yaml` changes. NWPS/NWRFC deferred — can layer in later without rework. |
| Active cutoff | **≥1 of flow/stage/temp reported within last 7 days** | Filters dormant/retired sites without dropping seasonal ones. Aligns with `_collect_gauge_rows` expired-row rule (>7 d stale → excluded from page anyway). |
| Discovery model | **Script + human review** | Pat eyeballs the candidate CSV and trims before commit; keeps maintenance surface small. |
| Output URL | `gauges.montana.html` | State-scoped variant of `gauges.html`. Builder is parameterized so future states get the same treatment for ~zero extra code. |

## Architecture overview

Three independent landings, each reviewable on its own:

1. **Discovery** — extend `scripts/fetch_usgs_sites.py` to cover Montana, then emit a candidate CSV filtered by HUC4 = 1701 and the 7-day-active rule.
2. **Migration** — hand-reviewed CSV → idempotent `data/db/migrations/0036_montana_usgs_gauges.sql` (pattern from `0027` Part B).
3. **Build code** — parameterize `_write_gauges_page(state=None)` so it can emit `gauges.<state-lower>.html`; call it once for `state="MT"` in `deploy.py`; add the URL to `_emit_sitemap`.

No `sources.yaml` change. No new parser. No new systemd unit. First hourly `fetch-usgs-ogc` after the migration populates observations; the next `levels build` writes the page.

## Phase 1: Discovery script

### Approach

`scripts/fetch_usgs_sites.py` already does ~90% of the work: it queries the USGS OGC site service per state, stores rows in `Gauge-metadata-cache/gauges.db` (table `usgs_site`), and back-fills `last_flow_date` / `last_gage_date` / `last_temp_date` from the time-series-metadata endpoint.

Changes:

- Add `"Montana"` to `STATES` in `scripts/fetch_usgs_sites.py:19`.
- **Skip the geographic post-filter for MT rows.** `fetch_usgs_sites.py:101` currently drops any site with `lat < 40 OR lon > -111`. The `lat < 40` check is moot in MT (whole state is north of 44°), but `lon > -111` would drop the eastern fringe of HUC 1701 (Glacier NP / Bob Marshall / North Fork Flathead headwaters all sit east of -111). Cleanest: skip the geographic filter entirely when `state_cd == 'MT'` — the HUC filter applied later is the real boundary.
- Add a candidate-pull SQL query (inlined in this plan, no new script needed): rows from `usgs_site` where `state_cd='MT'` AND `huc_cd LIKE '1701%'` AND any of `last_flow_date`/`last_gage_date`/`last_temp_date` is within 7 days of today.
- **Collision check before commit:** for each candidate `site_no`, verify it doesn't already exist in `gauge.name` in the live DB. `gauge.name` is `UNIQUE` (`models.py:122`), and the migration's `INSERT OR IGNORE` would *silently* skip a collision — masking the fact that an existing OR/WA/ID gauge is shadowing the new MT row. Existing usgs_ids start with 10/11/13/14; MT uses 12*, so a clash is unlikely but worth confirming.

Output: `data/discover/montana_candidates.csv` (gitignored — under `data/discover/` rather than `data/db/` to stay clear of the metadata-snapshot service's tree). Columns: `site_no, station_nm, latitude, longitude, huc_cd, drain_area_sq_mi, altitude_ft, last_flow_date, last_gage_date, last_temp_date`.

Pat reviews; deletes rows that are industrial monitoring / well sites / not paddler-relevant; the trimmed CSV feeds Phase 2.

### Reproduce

```bash
# Refresh the metadata cache including Montana
# (after the fetch_usgs_sites.py edits: add "Montana" + skip lon filter for MT)
python3 scripts/fetch_usgs_sites.py

# Pull the candidate list
mkdir -p data/discover
sqlite3 -header -csv Gauge-metadata-cache/gauges.db <<'SQL' > data/discover/montana_candidates.csv
SELECT site_no, station_nm, latitude, longitude, huc_cd,
       drain_area_sq_mi, altitude_ft,
       last_flow_date, last_gage_date, last_temp_date
FROM usgs_site
WHERE state_cd = '30'  -- FIPS code for Montana (state_cd stores FIPS, not abbreviations)
  AND huc_cd LIKE '1701%'
  AND (
    last_flow_date >= date('now','-7 days')
    OR last_gage_date >= date('now','-7 days')
    OR last_temp_date >= date('now','-7 days')
  )
ORDER BY huc_cd, site_no;
SQL

wc -l data/discover/montana_candidates.csv  # row count for load estimate

# Collision check against the live DB
sqlite3 -header /home/pat/DB/kayak.db <<SQL
SELECT g.id, g.name, g.state
FROM gauge g
WHERE g.name IN ($(awk -F, 'NR>1 {printf "%s\"%s\",", sep, $1; sep=""}' data/discover/montana_candidates.csv | sed 's/,$//'));
SQL
# Expected: no rows. Any hit is a manual-review case (almost certainly an
# existing ID gauge with the same number that needs special handling).
```

Cross-check the count against a direct USGS query (no DB involvement):

```bash
curl -s 'https://waterservices.usgs.gov/nwis/site/?format=rdb&stateCd=mt&hucCd=170101,170102,170103&siteType=ST&siteStatus=active&parameterCd=00060,00065,00010' \
  | awk '!/^#/ && NR>2 && $0!~/^5s/ {print $2}' | sort -u | wc -l
```

## Phase 2: Migration

### File: `data/db/migrations/0036_montana_usgs_gauges.sql`

Auto-generated from the trimmed CSV via a one-shot generator script (`scripts/generate_mt_migration.py`, gitignored — it's a build artifact, the SQL is the source of truth). The generator reads `data/discover/montana_candidates.csv`, applies the field-derivation rules below, and emits one `0036_montana_usgs_gauges.sql` file. Three idempotent inserts per site (pattern lifted from `0027` Part B):

```sql
-- 1. Source row: name = usgs_id, agency = 'USGS', no fetch_url
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '{site_no}', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (
    SELECT 1 FROM source WHERE name = '{site_no}' AND agency = 'USGS'
);

-- 2. Gauge row: usgs_id populated → picked up by fetch-usgs-ogc
INSERT OR IGNORE INTO gauge (
    name, location, latitude, longitude, usgs_id, station_id,
    river, display_name, sort_name, drainage_area, elevation,
    huc, allow_negative_flow, state
) VALUES (
    '{site_no}', '{location_parsed}', {lat}, {lon},
    '{site_no}', '{site_no}',
    '{river_parsed}', '{display_name_parsed}',
    '{sort_name}',
    {drain_area_sq_mi}, {altitude_ft},
    '{huc_cd}', 0, 'MT'
);

-- 3. gauge_source link
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '{site_no}' AND s.name = '{site_no}' AND s.agency = 'USGS';
```

### Field-derivation rules (gauge row)

- `name` / `station_id` / `usgs_id` / `display_name`: all `{site_no}` initially — editors can fill in human-readable names later via the UI.
- `river` / `location`: best-effort parse of USGS `station_nm`. The convention `"<RIVER> AT <LOCATION>, MT"` or `"<RIVER> NEAR <LOCATION>, MT"` is the dominant USGS format; the generator splits on " AT " / " NEAR " / " ABOVE " / " BELOW " and strips the trailing `, MT`. Sites that don't parse cleanly land in the migration with `river=''` / `location=station_nm` / `display_name=station_nm` (raw USGS name as fallback so the page row isn't blank) and get a `-- REVIEW:` comment in the migration.
- `sort_name`: `"<basin>|9|<10000−elev_ft, 6-digit zero-padded>|<drain_area_sq_mi, 6-digit zero-padded>"`. Basin = lowercase river-stem with internal spaces stripped (e.g. `clarkfork`, `bitterroot`, `flathead`, `kootenai`, `northforkflathead`). Mirrors the Oregon/Idaho pattern in `data/db/gauge.csv`. `_build_gauges_table` uses the first `|`-segment for the letter nav (`gauges.py:381`), so basin choice drives the A–Z grouping. **Must be non-empty for every row** — `_resolve_gauge_display` (`gauges.py:222-225`) returns `g.sort_name or river.lower()`; if both are empty the row sorts under "" and the letter index loses the entry. Fallback when `river` parse fails: `sort_name='zz_unparsed|9|999999|999999'` so unparseable rows sort *last* under "Z" and are visually distinct from real basins.
- **Avoid `;` in any string field** (location, river, display_name, sort_name). `cli/migrate.py::_split_statements` splits on `;` without parsing string literals — a semicolon in the SQL would split mid-statement and corrupt the migration. USGS station names use commas and parentheses, not semicolons, so this is a defensive guard for the generator rather than an observed problem.
- `huc`: store whatever USGS returns (typically 8-digit, occasionally less). The filter bar slices to HUC6 / HUC8 via prefix (`gauges.py:290-291`), so 8 digits is sufficient; no need to pad to 12. Column is `TEXT NULL` with no length constraint.
- `allow_negative_flow`: 0 (default; flip on per-site if the site is tidal — unlikely in HUC 1701).

### Verify before commit

```bash
# Dry-run on a sandbox DB copy
cp /home/pat/DB/kayak.db /tmp/kayak-sandbox.db
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db /home/pat/.venv/bin/levels migrate

# Confirm gauges landed and gauge_source links are populated
sqlite3 /tmp/kayak-sandbox.db <<'SQL'
SELECT COUNT(*) AS mt_gauges FROM gauge WHERE state = 'MT';
SELECT COUNT(*) AS mt_linked FROM gauge g
  JOIN gauge_source gs ON gs.gauge_id = g.id
  JOIN source s ON s.id = gs.source_id
  WHERE g.state = 'MT' AND s.agency = 'USGS';
-- mt_gauges and mt_linked must match; mismatch = a gauge missed its gauge_source insert
SELECT id, name, river, display_name FROM gauge WHERE state = 'MT' LIMIT 10;
SQL

# Confirm fetch-usgs-ogc picks up the new sites
DATABASE_URL=sqlite:////tmp/kayak-sandbox.db /home/pat/.venv/bin/levels fetch-usgs-ogc --dry-run --hours 12 2>&1 | tail -20
```

**Re-run safety:** `schema_migrations` records completion, so once 0036 lands you can't re-apply it with `levels migrate` alone. If you need to redo the verification on the same sandbox, `DELETE FROM schema_migrations WHERE version='0036'` first and then re-run. The `INSERT OR IGNORE` / `WHERE NOT EXISTS` guards make the SQL itself safe to re-execute; only the bookkeeping needs the manual reset.

`levels orphan-check` is intentionally **not** part of the verification: it
filters to sources with `fetch_url_id IS NOT NULL` (`src/kayak/db/sources.py:105`),
and our new USGS sources have `fetch_url_id = NULL` (USGS-OGC writes via the
gauge join, not a fetch_url). The migration cannot create orphans.

## Phase 3: State-scoped page builder

### Files touched

| Path | Change |
|---|---|
| `src/kayak/web/build/gauges.py` | `_write_gauges_page` gains a `state: str \| None = None` kwarg and returns `bool` (True if it wrote a page, False if the filtered row list was empty). When `state` is set: filter `rows` to `r["state"] == state` before building the table; if empty, return False without writing; otherwise pass `current_state=state`, `title=f"River Gauges — {state_full_name}"` (e.g. "River Gauges — Montana", looked up via `_ABBR_TO_STATE` in `_shared.py:54`), `path=f"/gauges.{state_lower}.html"`, write to `output_dir / f"gauges.{state_lower}.html"`. |
| `src/kayak/web/build/gauges.py` | `_build_gauges_filter_bar` gains an `is_all_page: bool = True` kwarg (currently hardcoded `True` at line 516); pass `False` from the state-scoped call. The existing `_build_filter_bar` mechanism (`levels.py:466`) already suppresses the state row when `is_all_page=False` — verified by `tests/test_build_filters.py::test_build_filter_bar_omits_state_on_single_state_page`. **No new flag needed.** |
| `src/kayak/web/build/deploy.py` | One additional call after the existing all-gauges call: `mt_written = _write_gauges_page(session, all_latest, states, css_link, output_dir, state="MT")`. **No `"MT" in states` guard** — `all_state_names()` returns only reach-states (`src/kayak/db/reaches.py:16`), and MT will have no reaches in this scope. The builder's internal row-count guard handles "nothing to write." |
| `src/kayak/web/build/deploy.py` | `_emit_sitemap` signature becomes `_emit_sitemap(output_dir, states, reaches, session, extra_urls: list[tuple[str, str, str]] \| None = None)`. The extra tuples are appended to the `urls` list before the XML render. Caller builds `extra_urls=[(f"{site}/gauges.montana.html", "hourly", "0.8")] if mt_written else None`. |

### Sparklines

`_write_gauges_page` already merges sparklines for any gauge not previously covered by the index build (gauges.py:556-570). The state-scoped call runs after the all-states call, so MT sparklines will already be in `sparklines.json` from that run. The merge is a defensive no-op in the steady state.

### Startup transient

First `levels build` after the migration but **before the first `fetch-usgs-ogc` run** will have zero MT entries in `all_latest`. `_collect_gauge_rows` returns an empty list → `_write_gauges_page(..., state="MT")` returns False → no page, no sitemap entry. The page materializes on the first build that follows a successful fetch run (typically within 1 hour of merging the migration).

### Letter nav edge case

The state-scoped page will have a small `letters` list (one per basin: K Kootenai, C Clark Fork, B Bitterroot/Blackfoot, F Flathead, ...). The existing `_build_gauges_table` builder needs no changes — it derives letters from sort_name basin prefixes per row. Confirm visually after the first build that the letter row isn't crowded into a single column.

### Test coverage to add

| Test | What it pins |
|---|---|
| `tests/test_build_gauges_state_filter.py::test_state_scoped_page_filters_rows` | Seed MT + OR gauges, call `_write_gauges_page(..., state="MT")`, assert the written `gauges.montana.html` contains the MT gauge id and not the OR one. |
| `tests/test_build_gauges_state_filter.py::test_state_scoped_page_returns_false_when_empty` | With no MT gauges in `all_latest`, the call returns False and no file is written. |
| `tests/test_build_filters.py` (extend) | `_build_gauges_filter_bar(rows, ..., is_all_page=False)` omits the `data-group="state"` block. Mirrors the existing `test_build_filter_bar_omits_state_on_single_state_page`. |

### State filter on gauges.html

The unfiltered `gauges.html` will start showing a Montana pill in its state filter once MT gauges are present (the filter is data-driven, see `_build_gauges_filter_bar` at gauges.py:482-516). That's the intended cross-link to the all-page experience.

### Discoverability gap

`gauges.montana.html` has **no inbound link** from the generated nav: `_NAV_STATES` (`src/kayak/web/build/_shared.py:57`) is `{Oregon, Washington, Idaho, Nevada, California}` and we're not adding MT to it (per the "reaches deferred" scope). The page lives as a deep URL — discoverable via direct entry or by clicking the Montana pill on `gauges.html` (which filters in-place but doesn't link out). If we want a cross-link, the lightest option is a small "view as standalone page" anchor inside the gauges-page filter bar that becomes visible when a single state pill is active; deferred to a follow-up unless the user wants it now.

## Load estimate

Current state: **150 gauges with `usgs_id`** in the DB (`data/db/gauge.csv`, NR>1, col 14 non-empty). `fetch-usgs-ogc` issues **ceil(N / 150) × 3 base HTTP calls** per hourly run (params 00060/00065/00010), plus 0–N pagination hops per batch where each page returns up to 10,000 features. Pagination is rarely hit in practice — 12 hours × ~96 readings × 150 sites = ~14 k obs per param-batch, which sometimes spills to a second page.

So today's load is ~3 base calls/hour, occasionally 4–6 with pagination.

| MT sites added | Total sites | Batches | Extra base calls/hour |
|---|---|---|---|
| +1 to +135 | 151–285 | 1 → 2 | **+3** |
| +136 to +285 | 286–435 | 2 → 3 | **+6** |

NWIS-OGC's quota is generous (hundreds per hour). At realistic MT counts (50–150 active sites), the worst case is +3 base calls/hour — a doubling of the OGC budget that's still well under the rate limit. Build-time cost scales linearly with row count and is dominated by sparkline rendering — single-digit seconds added.

Maintenance overhead is the real cost: each new gauge becomes another row that `scripts/audit_gauges.py` and the nightly audit eyeball. The 7-day filter keeps that small. `levels orphan-check` is unaffected (sources have `fetch_url_id IS NULL`).

**Expected first-run audit noise:** `scripts/audit_gauges.py` looks for "gauges that started providing data in the last week" (one of its four detector categories). Every newly-landed MT gauge will trip this on its first run after the migration. Mention in the PR description so the reviewer doesn't mistake the alert for a regression; subsequent runs settle.

## Files touched (final list)

| Path | Type |
|---|---|
| `scripts/fetch_usgs_sites.py` | edit — add `"Montana"` to `STATES`; relax/document the geographic post-filter |
| `data/discover/montana_candidates.csv` | new — gitignored, output of Phase 1 |
| `data/db/migrations/0036_montana_usgs_gauges.sql` | new — generated from trimmed CSV |
| `src/kayak/web/build/gauges.py` | edit — `state` kwarg on `_write_gauges_page` + filter-bar suppression |
| `src/kayak/web/build/deploy.py` | edit — extra `_write_gauges_page(..., state="MT")` call + sitemap entry |
| `tests/test_build_gauges_state_filter.py` | new — state-scoped page tests (see Phase 3 § Test coverage) |
| `tests/test_build_filters.py` | edit — extend with `_build_gauges_filter_bar(is_all_page=False)` assertion |
| `docs/PLAN_montana_gauges.md` | this file |
| `.gitignore` | edit — add `data/discover/` if not already covered |

## Follow-ups (deliberately not in this PR)

- **NWPS observed for MT forecast points.** A second pass can layer `nwps:` URLs for any MT LID that pairs with a kept USGS site (outage redundancy) or covers a gauge that has no USGS equivalent. Adds 1 HTTP call per LID, default concurrency 8 — trivial.
- **NWRFC textPlot / XML.** Only worth it for stations where USGS is dead/dropped a parameter and NWRFC computes flow via its local rating curve. Defer until a specific need appears.
- **Reaches + Montana.html.** If/when paddler-facing run pages are wanted, add reach rows, `reach_state` links, optionally `reach_class` and `reach_guidebook`, and add `"Montana"` to `_NAV_STATES` in `src/kayak/web/build/_shared.py:57`. That's a much bigger lift (descriptions, trace data, putin/takeout coords).
- **Generalize to other states.** Once `_write_gauges_page(state=...)` exists, `gauges.oregon.html`, `gauges.washington.html`, etc. cost one line each in `deploy.py`. Whether they add user value vs. the existing state filter on `gauges.html` is a separate UX question.
