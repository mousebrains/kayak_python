# One-off import and migration scripts

Scripts in this directory ran once (or a small number of times) to import
or repair specific data sets and are kept for historical reference rather
than active use. Each filename links back to the file's git history —
`git log --follow` shows the full context.

| Script | Last touched | What it did |
|---|---|---|
| `import_dreamflows.py` | 2026-04-11 | Scraped Dreamflows run pages and linked them back to reach rows by AW ID so their text descriptions could be surfaced alongside live flows. |
| `fix_sort_names.py` | 2026-04-11 | Backfilled `reach.sort_name` for older rows where the value was missing or inconsistent with the current naming convention. |
| `link_ok_guidebook.py` | 2026-04-11 | Populated `reach_guidebook` rows for the Oregon "Paddling Oregon" / "Soggy Sneakers" entries from a flat source list. |
| `backfill_gauge_huc.py` | 2026-04-23 | Filled `gauge.huc` for existing rows by joining gauge coordinates against the WBD HUC12 polygons. New gauges get HUCs at insert time via `levels assign-huc`. |
| `backfill_gauge_metadata.py` | 2026-04-19 | One-shot enrichment of `gauge.river` / `gauge.location` / `gauge.elevation` from the agency-metadata caches; the same resolver runs incrementally in `seed_gauge_display.py` for new gauges. |
| `backfill_gauge_state.py` | 2026-04-25 | Backfill for migration 0010 (added `gauge.state`). Tier 1: `usgs_site.state_cd` from the gauges metadata cache; tier 2: distinct state of any linked reach. New gauges get `state` at write time. |
| `recompute_midpoints.py` | 2026-04-20 | One-shot recompute of `reach.latitude` / `reach.longitude` as the arc-length midpoint of `reach.geom` (vs. the old straight-line midpoint of put-in/take-out). Subsequent geom updates compute the midpoint at write time. |
| `install-editor-feature.sh` | 2026-04-12 | Phase 1 sudo install steps for the editor / login feature (nginx zones, PHP-FPM pool, secrets dir). Subsumed by `hardening/install.sh` and the per-deploy nginx snippets under `deploy/`. |
| `split_usgs_sources.py` | 2026-05-05 | Gave every gauge with a `usgs_id` a dedicated USGS source named with the digit station ID (RENAME for 8 already-USGS sources, INSERT for 69 NWS-only gauges). Stops `fetch-usgs-ogc` from dumping USGS observations into NWS source rows where `_build_site_map` would otherwise fall back. |
| `backfill_usgs_nwis.py` | 2026-05-05 | Companion to `split_usgs_sources.py` — pulled 90 days of NWIS IV history for the freshly-split USGS sources so the new series had continuity from day one. 155 of 166 gauges returned data; 11 stations are inactive in NWIS. |
| `import_aw_usgs_reaches.py` | 2026-04-22 | Three-phase import of American Whitewater reaches keyed by USGS gauge: created missing `gauge` + `source` + `gauge_source` rows, inserted reach rows for unmatched AW reaches, and fetched AW geometry via the GraphQL API. Standalone (stdlib only). Companion to `match_aw_reaches.py`. |
| `match_aw_reaches.py` | 2026-04-22 | Matched American Whitewater reaches to local reaches via shared gauge source IDs. Cached AW reaches per state into `gauges.db`, then processed the cache to update `reach.aw_id`, put-in/take-out coordinates, and backfill missing `usgs_id` / `cbtt_id`. Output also drove the now-deleted `docs/wgb-matches.md`. |
| `install-observability.sh` | 2026-05-02 | Installed the nginx timed-log-format drop-in, the CSP violation report sink, and the logrotate config for the CSP log. Enabled per-route latency quantiles and CSP violation diffs for `/home/pat/logs.analyze/analyze.py`. Backups land under `/var/backups/kayak-nginx/`. |
| `fix_reach_class_data_type.py` | 2026-05-09 | Walked `reach_class` rows where `low_data_type='flow'` but the stored value (typically < 30) looked like a gage height in feet rather than CFS — proposed flipping `data_type` from `flow` to `gauge` on each affected field. Per-row y/n/q acceptance, with a kayak.db snapshot before any writes. |
| `apply-audit-timer-biweekly.sh` | 2026-05-11 | One-shot patch that installed the bi-weekly `kayak-audit-gauges` systemd schedule on the live server (commit `972f1d6`). |
| `apply-nginx-favicon.sh` | 2026-05-11 | One-shot patch that installed the `/favicon.ico` location block from `conf/levels.nginx` into live nginx (commit `49b2321`). |

## Archived experiments (subdirectories)

Small self-contained side projects kept for context. Each has its own
README describing the experiment and how to revive it.

| Subdirectory | Last touched | What it was |
|---|---|---|
| `map-color-tune/` | 2026-05-11 | Standalone three-pane Leaflet page that renders the same reach traces over OpenTopoMap, OpenStreetMap, and Esri Satellite so trace colors / casing / line-weight can be A/B'd against every basemap at once. Used while tuning the production `static/map.js` palette. See subdir README for redeploy steps. |
| `regressions-WhiteSalmon/` | 2026-03-01 | R `lm()` calibration of White Salmon at Husum (gauge) vs. White Salmon at Underwood (feet) — predates the calc-expression / rating-table flow. Inputs: `data` (tabular), outputs: `fit.pdf`. Rerun with `R --no-save < regress.R` from the subdir. |

## Running one again

You can still run these from the repo root via:

```bash
/home/pat/.venv/bin/python3 docs/one-offs/<script>.py
```

but review the code first — each assumes a specific state of the live
DB and external data that may have shifted since the last run.

## Adding a new one-off

1. Write the script under `scripts/` while iterating.
2. Once it has run and the outcome is captured in the DB, move it here
   with `git mv` and add a row to the table above.
