-- Migration 0023: drop 6 dead USACE merge-target source rows.
--
-- Background: `levels merge` was retired from the pipeline in commit
-- 2a710ee (2026-04-10, "Replace batch merge with gauge-level data
-- access"). Six USACE-tagged source rows on Willamette-system merge
-- gauges were the merge-target for `merge_sources()` (the lowest
-- source_id linked to each gauge). They have no `fetch_url_id` and
-- never had a live fetcher of their own; data only landed there when
-- merge was run manually, and the website hasn't read from them since
-- the 2026-04-10 cutover (PHP + gauge cache aggregate across all
-- linked sources directly).
--
-- The last manual `levels merge` was 2026-05-06, so these rows show
-- up as stale on status.php's per-agency bucket while contributing
-- nothing to the actual displayed data. Dropping them.
--
-- Affected sources (id, name, gauge):
--   74  NESTUCCA_RIVER_NEAR_BEAVER           gauge 75  Nestucca_Beaver_merge
--   100 S_SANTIAM_RIVER_AT_WATERLOO          gauge 106 S_Santiam_Waterloo_merge
--   102 SANTIAM_RIVER_AT_JEFFERSON           gauge 108 Santiam_Jefferson_merge
--   127 WILLAMETTE_RIVER_AT_EUGENE           gauge 137 EUGENE_merge
--   134 WILLAMETTE_RIVER_AT_SALEM            gauge 142 SALEM_merge
--   139 WILLAMETTE_RIVER_AT_OREGON_CITY_UPR  gauge 145 Willamette_upper_falls_merge
--
-- Pre-flight (per docs/migrations.md):
--   - fetch_url_id is NULL for all 6 — no fetch_url cleanup needed.
--   - No calc_expression references any of these gauges by name.
--   - latest_gauge_observation.source_id already points at the live
--     NWS/USGS siblings for every (gauge, data_type), so the website
--     cache doesn't need a rebuild for correctness — it's already
--     bypassing these rows.
--   - Each affected gauge keeps its live NWS + USGS sources.

-- observation.source_id is ON DELETE RESTRICT, so clear obs rows first.
DELETE FROM observation WHERE source_id IN (74, 100, 102, 127, 134, 139);

-- gauge_source and latest_observation cascade on source delete.
DELETE FROM source WHERE id IN (74, 100, 102, 127, 134, 139);
