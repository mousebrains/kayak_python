-- Migration 0071: drop the Bridgeport Columbia gauge (USGS 12438000).
--
-- Bridgeport (RM 544) sits ABOVE John Day Dam, outside the downstream-of-John-
-- Day-Dam scope the Columbia corridor is being trimmed to. It was added as a
-- temperature-only USGS gauge in migration 0066 (gauge '12438000' + USGS source
-- '12438000' + gauge_source). Remove all three.
--
-- Pre-flight (per docs/migrations.md): no calc_expression input references
-- '12438000'; no reach.gauge_id points at it; nothing in data/sources.yaml or
-- src/ references it (it was fetched by fetch-usgs-ogc via the gauge's usgs_id,
-- which is removed with the gauge -- no fetch_url row exists to clean up).
--
-- Delete order: observation.source_id is ON DELETE RESTRICT, so observation rows
-- go first; gauge_source + latest_observation cascade on the source delete;
-- latest_gauge_observation cascades on the gauge delete.
--
-- Deleted BY NAME (not the prod-assigned id) so it is portable AND so the R4.4
-- reconciliation guard's _deleted_sources() can see it: 0066's INSERT still
-- wires '12438000', so the guard would otherwise expect it in source.csv after
-- this PR removes that row. The data/db/{gauge,source,gauge_source}.csv rows are
-- removed in the same PR to keep a from-scratch rebuild consistent with prod.
--
-- No @no_transaction needed (only colon-free statements).

DELETE FROM observation
WHERE source_id IN (SELECT id FROM source WHERE name = '12438000' AND agency = 'USGS');

DELETE FROM source WHERE name = '12438000' AND agency = 'USGS';

DELETE FROM gauge WHERE name = '12438000';
