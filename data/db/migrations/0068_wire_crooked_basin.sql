-- Migration 0068: wire 4 Crooked-basin NWS/NWRFC gauges + NF Crooked reach (Batch C).
--
-- Adds:
--   CRPO3  NWS    Crooked abv Prineville Resv   (anchors new NF Crooked reach)
--   PRVO3  NWS    Crooked blw Prineville Resv   (gauge-only; no AW reach yet)
--   CRSO3  NWRFC  Crooked at Smith Rock         (gauge-only; USBR-via-NWRFC textplot)
--   OCHO3  NWS    Ochoco Creek blw Ochoco Resv  (gauge-only; Crooked trib)
--
-- Reach 14 (Lone Pine -> Crooked R Ranch) stays on its existing 14087380 take-out gauge per
-- boater convention of reading the take-out, not the mid-reach. CRSO3 sits inside reach 14 but
-- is not re-pointed here.
--
-- Adds 1 reach: aw_11293 NF Crooked 'Deep Creek to SE Paulina Hwy', Class III+(V), 26.47 mi,
-- 848 ft drop, max gradient 62 ft/mi, anchored on CRPO3. State OR.
--
-- UPDATEs the 2 existing mainstem Crooked reach sort_names to the Sandy-basin convention so
-- the new NF Crooked reach sorts ahead of them upstream->downstream (a < b):
--   aw_1503  'Crooked 01' -> 'Crooked b 01 Lone Pine to Crooked R Ranch'
--   aw_3759  'Crooked 02' -> 'Crooked b 02 Crooked R Ranch to Billy Chinook'
--
-- 3 of 4 gauges (CRPO3, PRVO3, OCHO3) fetch via the NWPS API (parser='nwps', agency='NWS');
-- CRSO3 is USBR-via-NWRFC (not on the NWPS API), so it goes through the NWRFC textPlot CGI
-- (parser='nwrfc.textplot', agency='NWRFC', pe=HG returns paired Stage + computed Discharge,
-- same pattern as EUGO3). All 4 URLs added to data/sources.yaml in this PR.
--
-- reach.geom + reach.gradient_profile for aw_11293 land via data/db/reaches.json +
-- reaches-gradient.json (the documented JSON exception; CLAUDE.md / R6.1). Trace built from
-- AW-cache coords refined by the right-click latlon tool; neither splice nor snap was needed
-- (sig_frac 98% on the gradient profile, no NHD HR side-channel routing).
--
-- R4.4: the 4 new source names belong in PENDING_RECONCILIATION
-- (tests/test_scripts/test_migration_csv_reconciliation.py) until the nightly snapshot lands
-- them in data/db/source.csv. Same pattern as 0065/0066/0067.
--
-- Idempotent: gauge.name UNIQUE -> INSERT OR IGNORE; source.name non-unique -> NOT EXISTS guard
-- on (name, agency); fetch_url.url UNIQUE -> INSERT OR IGNORE; gauge_source composite PK ->
-- INSERT OR IGNORE; reach.name UNIQUE -> INSERT OR IGNORE; reach_state composite PK -> INSERT
-- OR IGNORE; reach_class no constraint -> NOT EXISTS guard. UPDATEs on existing reaches are
-- keyed by aw_id (immutable). Linked by name/url throughout, so prod-assigned ids are fine.

-- gauge CRPO3 (Crooked abv Prineville Resv)  via parser='nwps' agency='NWS'
INSERT OR IGNORE INTO fetch_url (url, parser, is_active) VALUES
    ('https://api.water.noaa.gov/nwps/v1/gauges/CRPO3/stageflow/observed', 'nwps', 1);
INSERT OR IGNORE INTO gauge
    (name, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('CRPO3', 44.179166666667, -120.58777777778,
     'Crooked', 'abv Prineville Resv', 'Crooked abv Prineville Resv',
     'crooked|9|005000|000000', 'OR', '17070304', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'CRPO3', 'NWS', fu.id, NULL, NULL FROM fetch_url fu
WHERE fu.url = 'https://api.water.noaa.gov/nwps/v1/gauges/CRPO3/stageflow/observed'
  AND NOT EXISTS (SELECT 1 FROM source s WHERE s.name = 'CRPO3' AND s.agency = 'NWS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'CRPO3' AND s.name = 'CRPO3' AND s.agency = 'NWS';

-- gauge PRVO3 (Crooked blw Prineville Resv)  via parser='nwps' agency='NWS'
INSERT OR IGNORE INTO fetch_url (url, parser, is_active) VALUES
    ('https://api.water.noaa.gov/nwps/v1/gauges/PRVO3/stageflow/observed', 'nwps', 1);
INSERT OR IGNORE INTO gauge
    (name, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('PRVO3', 44.113888888889, -120.79444444444,
     'Crooked', 'blw Prineville Resv', 'Crooked blw Prineville Resv',
     'crooked|9|006500|000000', 'OR', '17070305', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'PRVO3', 'NWS', fu.id, NULL, NULL FROM fetch_url fu
WHERE fu.url = 'https://api.water.noaa.gov/nwps/v1/gauges/PRVO3/stageflow/observed'
  AND NOT EXISTS (SELECT 1 FROM source s WHERE s.name = 'PRVO3' AND s.agency = 'NWS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'PRVO3' AND s.name = 'PRVO3' AND s.agency = 'NWS';

-- gauge CRSO3 (Crooked at Smith Rock)  via parser='nwrfc.textplot' agency='NWRFC'
INSERT OR IGNORE INTO fetch_url (url, parser, is_active) VALUES
    ('https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=CRSO3&pe=HG', 'nwrfc.textplot', 1);
INSERT OR IGNORE INTO gauge
    (name, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('CRSO3', 44.368057, -121.138816,
     'Crooked', 'Smith Rock', 'Crooked at Smith Rock',
     'crooked|9|007500|000000', 'OR', '17070305', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'CRSO3', 'NWRFC', fu.id, NULL, NULL FROM fetch_url fu
WHERE fu.url = 'https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=CRSO3&pe=HG'
  AND NOT EXISTS (SELECT 1 FROM source s WHERE s.name = 'CRSO3' AND s.agency = 'NWRFC');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'CRSO3' AND s.name = 'CRSO3' AND s.agency = 'NWRFC';

-- gauge OCHO3 (Ochoco Creek blw Ochoco Resv)  via parser='nwps' agency='NWS'
INSERT OR IGNORE INTO fetch_url (url, parser, is_active) VALUES
    ('https://api.water.noaa.gov/nwps/v1/gauges/OCHO3/stageflow/observed', 'nwps', 1);
INSERT OR IGNORE INTO gauge
    (name, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('OCHO3', 44.396108, -120.431016,
     'Ochoco Creek', 'blw Ochoco Resv', 'Ochoco Creek blw Ochoco Resv',
     'ochoco|9|005000|000000', 'OR', '17070304', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'OCHO3', 'NWS', fu.id, NULL, NULL FROM fetch_url fu
WHERE fu.url = 'https://api.water.noaa.gov/nwps/v1/gauges/OCHO3/stageflow/observed'
  AND NOT EXISTS (SELECT 1 FROM source s WHERE s.name = 'OCHO3' AND s.agency = 'NWS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'OCHO3' AND s.name = 'OCHO3' AND s.agency = 'NWS';

-- reach aw_11293  -- Crooked a 01 Deep Creek to SE Paulina Hwy
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_11293', 'NF Crooked', 'Crooked a 01 Deep Creek to SE Paulina Hwy', 'Crooked',
    g.id, 'Deep Creek to SE Paulina Hwy', 'III+(V)',
    'Crooked', 26.474801856796972, 32.0, 62.0,
    4339, 848,
    44.328004, -120.078174,
    44.116803, -120.245866,
    44.2224035, -120.16202000000001,
    11293, '17070304', 0
FROM gauge g WHERE g.name = 'CRPO3';

INSERT OR IGNORE INTO reach_state (reach_id, state_id)
SELECT r.id, st.id FROM reach r, state st WHERE r.name = 'aw_11293' AND st.abbreviation = 'OR';

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'III+(V)', NULL, 'flow', NULL, 'flow'
FROM reach r WHERE r.name = 'aw_11293'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'III+(V)');

-- Re-key existing mainstem Crooked reaches to the Sandy convention.
UPDATE reach SET sort_name = 'Crooked b 01 Lone Pine to Crooked R Ranch' WHERE aw_id = 1503;
UPDATE reach SET sort_name = 'Crooked b 02 Crooked R Ranch to Billy Chinook' WHERE aw_id = 3759;
