-- Migration 0026: drop WCFO3 (Willamette at Canby Ferry) — added today
-- without ever being linked to a reach, and its upstream gauge feed is
-- sporadic enough that we don't want to keep fetching it speculatively.
--
-- Chain: reach (none) → gauge 189 Willamette_Canby_Ferry → source 305
-- WCFO3 (NWS, nwps) → fetch_url 119
-- (https://api.water.noaa.gov/nwps/v1/gauges/WCFO3/stageflow/observed)
--
-- Pre-flight (per docs/migrations.md):
--   - fetch_url 119 has exactly one source (305) — no other consumer;
--     the URL is also being removed from data/sources.yaml in the same
--     change, so sync_sources will not recreate it.
--   - No calc_expression references the gauge name.
--   - No reach.gauge_id points at gauge 189.
--   - gauge_source, observation, latest_observation cascade on source
--     delete (observation is RESTRICT, so its rows go first).
--   - latest_gauge_observation cascades on gauge delete.

-- observation.source_id is ON DELETE RESTRICT, so clear obs rows first.
DELETE FROM observation WHERE source_id = 305;

-- gauge_source + latest_observation cascade on source delete.
DELETE FROM source WHERE id = 305;

-- latest_gauge_observation cascades on gauge delete.
DELETE FROM gauge WHERE id = 189;

-- The fetch_url is no longer referenced by any source and is removed
-- from data/sources.yaml in the same commit; drop the row outright.
DELETE FROM fetch_url WHERE id = 119;
