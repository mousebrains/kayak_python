-- Migration 0066: wire 4 Columbia River mainstem USGS temperature gauges (Batch A).
--
-- Adds the Columbia mainstem's temperature-bearing USGS gauges as gauge-only
-- entries (Shape 1, per docs/PLAN_add_gauges_reaches.md). The goal is live water
-- temperature (paddling-relevant); flow/stage-only mainstem sites are out of
-- scope. Zero parser / sources.yaml work: any gauge with a usgs_id is fetched by
-- `levels fetch-usgs-ogc` (params 00060/00065/00010, temp C->F). Each gauge is a
-- USGS source (agency 'USGS', fetch_url_id NULL, name = the station id) plus a
-- gauge_source link.
--
-- River-mile-ordered, upstream -> downstream:
--   12438000          Bridgeport          RM 544  WA      temp
--   454249120423500   below John Day Dam  RM 215  OR,WA   temp  (USGS "near Cliffs")
--   14105700          The Dalles          RM 192  OR,WA   flow+stage+temp
--   Bonneville_merge  below Bonneville Dam  RM 145  OR,WA  stage+temp  (14128870 + Cascade Island 453845121564001)
--
-- For the two dams the temperature monitor is the DOWNSTREAM water-quality site
-- (Cliffs below John Day; Cascade Island below Bonneville), not the forebay or
-- nav lock. Bonneville is a MERGE: stage (14128870, tailwater) + temperature
-- (Cascade Island 453845121564001) on one gauge, aggregated -- the Wind pattern.
-- This relies on the source-based fetcher (the 0065_split PR), which fetches each
-- USGS source by name rather than one usgs_id per gauge.
--
-- STATE + HUC: gauges.html emits a row's state/HUC filter only when BOTH
-- gauge.state AND gauge.huc(8) are set, so every gauge carries its huc8. Below
-- McNary the Columbia is the OR/WA line, so those three gauges set
-- state='OR,WA'; gauges.py splits the comma list into one data-state per state
-- (the same way the reach table joins reach.states), so a border gauge filters
-- under both WA and OR. Bridgeport is above McNary, where the Columbia is
-- WA-internal -> 'WA' only.
--
-- sort_name is set by river mile so the corridor lists upstream->downstream;
-- do NOT run scripts/seed_gauge_display.py on these gauges -- it would recompute
-- sort_name from the unreliable big-river USGS elevations and clobber the order.
--
-- Idempotent: gauge.name UNIQUE -> INSERT OR IGNORE; source.name non-unique ->
-- NOT EXISTS guard on (name, agency); gauge_source composite PK -> INSERT OR
-- IGNORE. Linked by name, so prod-assigned ids are fine. No colon/semicolon/--
-- inside any literal.
--
-- R4.4: the 5 USGS source names are in PENDING_RECONCILIATION
-- (tests/test_scripts/test_migration_csv_reconciliation.py) until the nightly
-- snapshot lands them in data/db/source.csv; a follow-up PR drops them.

-- 1. Bridgeport, RM 544, WA
INSERT OR IGNORE INTO gauge
    (name, usgs_id, latitude, longitude, river, location, display_name, sort_name, state, huc, allow_negative_flow)
VALUES
    ('12438000', '12438000', 48.0065333, -119.6653303, 'Columbia',
     'Bridgeport', 'Columbia at Bridgeport',
     'columbia|9|000456|000000', 'WA', '17020005', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12438000', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '12438000' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '12438000' AND s.name = '12438000' AND s.agency = 'USGS';

-- 2. below John Day Dam (USGS "right bank, near Cliffs"), RM 215, OR/WA border
INSERT OR IGNORE INTO gauge
    (name, usgs_id, latitude, longitude, river, location, display_name, sort_name, state, huc, allow_negative_flow)
VALUES
    ('454249120423500', '454249120423500', 45.7134579, -120.710893, 'Columbia',
     'Below John Day Dam', 'Columbia below John Day Dam',
     'columbia|9|000785|000000', 'OR,WA', '17070105', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '454249120423500', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '454249120423500' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '454249120423500' AND s.name = '454249120423500' AND s.agency = 'USGS';

-- 3. The Dalles, RM 192, OR/WA border
INSERT OR IGNORE INTO gauge
    (name, usgs_id, latitude, longitude, river, location, display_name, sort_name, state, huc, allow_negative_flow)
VALUES
    ('14105700', '14105700', 45.60827778, -121.1899167, 'Columbia',
     'The Dalles', 'Columbia at The Dalles',
     'columbia|9|000808|000000', 'OR,WA', '17070105', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '14105700', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '14105700' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '14105700' AND s.name = '14105700' AND s.agency = 'USGS';

-- 4. below Bonneville Dam -- MERGE of two USGS sources on one gauge (the Wind
--    pattern): stage from 14128870 (tailwater) + temperature from 453845121564001
--    (Cascade Island, just below the dam). RM ~145, OR/WA border. usgs_id is set
--    to the stage station for the USGS link; the source-based fetcher fetches both.
INSERT OR IGNORE INTO gauge
    (name, usgs_id, latitude, longitude, river, location, display_name, sort_name, state, huc, allow_negative_flow)
VALUES
    ('Bonneville_merge', '14128870', 45.63305556, -121.9608333, 'Columbia',
     'Below Bonneville Dam', 'Columbia below Bonneville Dam',
     'columbia|9|000855|000000', 'OR,WA', '17080001', 0);
-- stage (below-dam tailwater)
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '14128870', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '14128870' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Bonneville_merge' AND s.name = '14128870' AND s.agency = 'USGS';
-- temperature (Cascade Island)
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '453845121564001', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '453845121564001' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Bonneville_merge' AND s.name = '453845121564001' AND s.agency = 'USGS';
