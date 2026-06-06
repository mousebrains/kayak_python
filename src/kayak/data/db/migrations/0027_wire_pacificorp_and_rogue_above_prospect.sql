-- Migration 0027: wire PacifiCorp Rogue Bypass + revive Rogue-above-Prospect
-- estimate via 14330000 linear fit (slope=0.8285, intercept=-292.72,
-- r2=0.9575, RMSE=117 cfs, n=8599 daily means / ~23.5 years of overlap,
-- non-contiguous 1985-01-01..2024-06-09; see docs/PLAN_pacificorp_rogue.md
-- for the regression methodology).
--
-- Idempotency: tables with a UNIQUE constraint (fetch_url.url, gauge.name,
-- gauge_source PK) use INSERT OR IGNORE. Source.name is intentionally
-- non-unique (per models.py::Source) and calc_expression has no UNIQUE
-- constraint either, so those inserts guard via WHERE NOT EXISTS.

------------------------------------------------------------------------------
-- PART A: PacifiCorp bypass — fetch_url, source, gauge, gauge_source, reach.
------------------------------------------------------------------------------

INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'pacificorp',
    'https://www.pacificorp.com/etc/pcorp/datafiles/hydro/RogueRiverBypass.xml',
    '',
    1
);

INSERT INTO source (name, agency, fetch_url_id, timezone)
SELECT 'PR2R.NFD_BYP_80FL_PI', 'pacificorp', fu.id, 'America/Los_Angeles'
FROM fetch_url fu
WHERE fu.url = 'https://www.pacificorp.com/etc/pcorp/datafiles/hydro/RogueRiverBypass.xml'
  AND NOT EXISTS (
      SELECT 1 FROM source s
      WHERE s.name = 'PR2R.NFD_BYP_80FL_PI' AND s.fetch_url_id = fu.id
  );

INSERT OR IGNORE INTO gauge (
    name, location, latitude, longitude, river, display_name, sort_name,
    huc, allow_negative_flow, state
) VALUES (
    'NF_Rogue_Bypass',
    'OR-62 Bridge below North Fork Diversion Dam',
    42.74008, -122.49605,
    'Rogue River', 'NF Rogue Bypass below NF Dam',
    'rogue|9|008100|999998',
    '171003070113', 0, 'OR'
);

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'NF_Rogue_Bypass' AND s.name = 'PR2R.NFD_BYP_80FL_PI';

UPDATE reach SET gauge_id = (SELECT id FROM gauge WHERE name = 'NF_Rogue_Bypass')
WHERE id = 68;

------------------------------------------------------------------------------
-- PART B: USGS 14330000 — gauge + source + gauge_source link.
--
-- fetch-usgs-ogc (cli/fetch_usgs_ogc.py::_build_site_map) only writes
-- observations to sources reachable via gauge → gauge_source → source,
-- where the gauge has usgs_id set. Creating only the gauge would leave
-- the data path broken. The Source row pattern matches existing USGS
-- entries: name=usgs_id, agency='USGS', fetch_url_id=NULL (USGS-OGC
-- writes via the gauge join, not a fetch_url).
------------------------------------------------------------------------------

INSERT OR IGNORE INTO gauge (
    name, location, latitude, longitude, usgs_id, station_id, river,
    display_name, sort_name, elevation, huc, allow_negative_flow, state
) VALUES (
    '14330000',
    'Below Prospect',
    42.72957, -122.51615,
    '14330000', '14330000',
    'Rogue River', 'Rogue below Prospect',
    'rogue|9|007590|999990',
    1964.56, '17100307', 0, 'OR'
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '14330000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (
    SELECT 1 FROM source WHERE name = '14330000' AND agency = 'USGS'
);

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '14330000' AND s.name = '14330000' AND s.agency = 'USGS';

------------------------------------------------------------------------------
-- PART C: New calc gauge: Rogue_Above_Prospect_calc.
--   14328000_est = 0.8285 * 14330000_flow - 292.72
--   r2=0.9575, RMSE=117 cfs, n=8599 daily means (non-contiguous
--   1985-01-01..2024-06-09; ~23.5 years of overlap data, with a gap
--   1998-2014 when 14328000 was offline).
--   Replaces the retired USGS 14328000 (last data 2024-06-09).
--   See docs/PLAN_pacificorp_rogue.md.
------------------------------------------------------------------------------

INSERT INTO calc_expression (data_type, expression, time_expression, note)
SELECT
    'flow',
    'round(greatest(0, 0.8285 * rp::14330000::flow - 292.72))',
    'rp::14330000::flow',
    'Rogue above Prospect (14328000 retired 2024-06-09) estimated from 14330000 via linear fit. n=8599 daily means / ~23.5 yr overlap (non-contiguous 1985-01-01..2024-06-09), r2=0.9575, RMSE=117 cfs. Fit is stable across 1970-2024 sub-windows (slope 0.83-0.84, r2 0.96).'
WHERE NOT EXISTS (
    SELECT 1 FROM calc_expression WHERE time_expression = 'rp::14330000::flow'
);

INSERT OR IGNORE INTO gauge (
    name, location, latitude, longitude, river,
    display_name, sort_name, elevation, huc, allow_negative_flow, state
) VALUES (
    'Rogue_Above_Prospect_calc',
    'Above Prospect (estimated)',
    42.77485, -122.49976,
    'Rogue River', 'Rogue above Prospect (calc)',
    'rogue|9|007380|999991',
    2620.0, '171003070113', 0, 'OR'
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'Rogue_Above_Prospect_calc', 'Calculation', NULL, ce.id, ''
FROM calc_expression ce
WHERE ce.time_expression = 'rp::14330000::flow'
  AND NOT EXISTS (
      SELECT 1 FROM source s WHERE s.name = 'Rogue_Above_Prospect_calc'
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Rogue_Above_Prospect_calc' AND s.name = 'Rogue_Above_Prospect_calc';

------------------------------------------------------------------------------
-- PART D: Re-point reaches 67 (River Bridge → was gauge 87 dormant calc),
-- 161 (Takelma → was gauge 88 retired USGS), 307 (Natural Bridge → was
-- gauge 88) to the new Rogue_Above_Prospect_calc gauge.
-- Gauges 87, 88, 89 stay in place but become unreferenced by these reaches
-- after this UPDATE — a follow-up migration can prune them.
------------------------------------------------------------------------------

UPDATE reach SET gauge_id = (SELECT id FROM gauge WHERE name = 'Rogue_Above_Prospect_calc')
WHERE id IN (67, 161, 307);
