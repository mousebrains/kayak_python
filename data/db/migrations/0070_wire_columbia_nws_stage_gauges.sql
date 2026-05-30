-- Migration 0070: add two lower-Columbia NWS stage gauges (Vancouver, St. Helens).
--
-- Two mainstem stage gauges on the OR/WA border below the Willamette confluence,
-- both NWS forecast points read by the nwps parser (stage only -- the tidal lower
-- river has no meaningful discharge). Gauge-only adds (Shape 1, per
-- docs/PLAN_add_gauges_reaches.md), river-mile-ordered:
--   VAPW1  Vancouver    RM 106
--   SHNO3  St. Helens   RM 86
--
-- NWS fetch source: agency 'NWS', parser 'nwps', with the NWPS stageflow URL in
-- BOTH data/sources.yaml (the pipeline only fetches URLs listed there) and the
-- fetch_url row below. The source is pre-created here so the first fetch resolves
-- it via source_map -- otherwise the parser would auto-create an agency-less
-- orphan source (the failure mode docs/migrations.md warns about).
--
-- STATE + HUC: gauges.html emits a row's state/watershed filter only when BOTH
-- gauge.state and gauge.huc (>=8 digits) are set. Below McNary the Columbia is
-- the OR/WA line, so state='OR,WA' (gauges.py splits the comma list into one
-- data-state per state, so the gauge filters under both). huc is HUC8 17080003
-- (Lower Columbia-Clatskanie), from the nearest mainstem USGS sites 14144700 /
-- 14222870.
--
-- sort_name is set by river mile (columbia|9|<1000-RM>|000000) so the corridor
-- lists upstream->downstream with the existing Columbia gauges; do NOT run
-- scripts/seed_gauge_display.py on these -- it would recompute sort_name from the
-- tidal river's ~sea-level elevation (and the NWS gauges carry no elevation) and
-- clobber the order. build reads sort_name and never recomputes it.
--
-- Idempotent: gauge.name UNIQUE + fetch_url.url UNIQUE -> INSERT OR IGNORE;
-- source.name non-unique -> NOT EXISTS guard on (name, fetch_url_id);
-- gauge_source composite PK -> INSERT OR IGNORE. Linked by name/URL, so
-- autoincrement ids are fine. Only colon is the https:// scheme.
--
-- New source names (VAPW1, SHNO3) in PENDING_RECONCILIATION
-- (tests/test_scripts/test_migration_csv_reconciliation.py) until the nightly
-- snapshot lands them in data/db/source.csv; a follow-up PR drops them.

-- Vancouver, RM 106
INSERT OR IGNORE INTO gauge
    (name, nwsli_id, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('VAPW1', 'VAPW1', 45.631, -122.696, 'Columbia',
     'Vancouver', 'Columbia at Vancouver',
     'columbia|9|000894|000000', 'OR,WA', '17080003', 0);

INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'nwps', 'https://api.water.noaa.gov/nwps/v1/gauges/VAPW1/stageflow/observed', '', 1
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'VAPW1', 'NWS', fu.id, NULL, NULL
FROM fetch_url fu
WHERE fu.url = 'https://api.water.noaa.gov/nwps/v1/gauges/VAPW1/stageflow/observed'
  AND NOT EXISTS (SELECT 1 FROM source s WHERE s.name = 'VAPW1' AND s.fetch_url_id = fu.id);

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s WHERE g.name = 'VAPW1' AND s.name = 'VAPW1';

-- St. Helens, RM 86
INSERT OR IGNORE INTO gauge
    (name, nwsli_id, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('SHNO3', 'SHNO3', 45.864, -122.796, 'Columbia',
     'St. Helens', 'Columbia at St. Helens',
     'columbia|9|000914|000000', 'OR,WA', '17080003', 0);

INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'nwps', 'https://api.water.noaa.gov/nwps/v1/gauges/SHNO3/stageflow/observed', '', 1
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'SHNO3', 'NWS', fu.id, NULL, NULL
FROM fetch_url fu
WHERE fu.url = 'https://api.water.noaa.gov/nwps/v1/gauges/SHNO3/stageflow/observed'
  AND NOT EXISTS (SELECT 1 FROM source s WHERE s.name = 'SHNO3' AND s.fetch_url_id = fu.id);

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s WHERE g.name = 'SHNO3' AND s.name = 'SHNO3';
