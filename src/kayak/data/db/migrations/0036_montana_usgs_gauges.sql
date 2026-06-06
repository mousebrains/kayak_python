-- Migration 0036: Montana USGS gauges (curated list).
--
-- Site numbers pulled from montana/mt.list (hand-curated by Pat from
-- the entries circled on https://levels-legacy.wkcc.org/?P=Montana.html).
-- Per-site metadata pulled from Gauge-metadata-cache/gauges.db::usgs_site.
-- See docs/PLAN_montana_gauges.md.
--
-- Idempotent: re-running is safe (INSERT OR IGNORE / WHERE NOT EXISTS).

-- Belt Creek near Monarch MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06090500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06090500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06090500', 'near Monarch', 47.20859444444445, -110.93174722222223, '06090500', '06090500', 'Belt Creek', 'Belt Creek near Monarch', 'beltcreek|9|006020|000355', 355.0, 3980.0, '100301050306', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06090500' AND s.name = '06090500' AND s.agency = 'USGS';

-- Big Hole River near Melrose MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06025500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06025500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06025500', 'near Melrose', 45.5265805555556, -112.701725, '06025500', '06025500', 'Big Hole', 'Big Hole near Melrose', 'bighole|9|004967|002472', 2472.0, 5032.87, '100200041209', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06025500' AND s.name = '06025500' AND s.agency = 'USGS';

-- Big Hole River at Maiden Rock nr Divide MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06025250', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06025250' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06025250', 'at Maiden Rock near Divide', 45.7012694444444, -112.735969444444, '06025250', '06025250', 'Big Hole', 'Big Hole at Maiden Rock near Divide', 'bighole|9|004700|002199', 2199.0, 5300.0, '100200041104', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06025250' AND s.name = '06025250' AND s.agency = 'USGS';

-- Blackfoot River near Bonner MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12340000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12340000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12340000', 'near Bonner', 46.899411111111114, -113.75631944444444, '12340000', '12340000', 'Blackfoot', 'Blackfoot near Bonner', 'blackfoot|9|006655|002287', 2287.0, 3344.76, '170102031308', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12340000' AND s.name = '12340000' AND s.agency = 'USGS';

-- Clark Fork at St. Regis MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12354500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12354500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12354500', 'at St. Regis', 47.3016388888889, -115.086869444444, '12354500', '12354500', 'Clark Fork', 'Clark Fork at St. Regis', 'clarkfork|9|007400|010728', 10728.0, 2600.37, '170102040805', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12354500' AND s.name = '12354500' AND s.agency = 'USGS';

-- Dearborn River near Craig MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06073500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06073500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06073500', 'near Craig', 47.199025, -112.095905555556, '06073500', '06073500', 'Dearborn', 'Dearborn near Craig', 'dearborn|9|006200|000322', 322.0, 3800.0, '100301020404', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06073500' AND s.name = '06073500' AND s.agency = 'USGS';

-- S F Flathead R ab Twin C nr Hungry Horse MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12359800', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12359800' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12359800', 'above Twin C near Hungry Horse', 47.9790972222222, -113.560683333333, '12359800', '12359800', 'South Fork Flathead R', 'South Fork Flathead R above Twin C near Hungry Horse', 'southforkflatheadr|9|006425|001159', 1159.0, 3575.0, '170102090509', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12359800' AND s.name = '12359800' AND s.agency = 'USGS';

-- Jefferson River near Three Forks MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06036650', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06036650' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06036650', 'near Three Forks', 45.89713611111111, -111.59567222222222, '06036650', '06036650', 'Jefferson', 'Jefferson near Three Forks', 'jefferson|9|005920|009558', 9558.0, 4079.5, '100200050805', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06036650' AND s.name = '06036650' AND s.agency = 'USGS';

-- Madison River at Kirby Ranch nr Cameron MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06038800', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06038800' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06038800', 'at Kirby Ranch near Cameron', 44.88865555555555, -111.58088611111111, '06038800', '06038800', 'Madison', 'Madison at Kirby Ranch near Cameron', 'madison|9|004127|001093', 1093.0, 5872.92, '100200070703', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06038800' AND s.name = '06038800' AND s.agency = 'USGS';

-- Missouri River bl Holter Dam nr Wolf Cr MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06066500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06066500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06066500', 'below Holter Dam near Wolf Creek', 46.9947388888889, -112.010666666667, '06066500', '06066500', 'Missouri', 'Missouri below Holter Dam near Wolf Creek', 'missouri|9|006536|016924', 16924.0, 3464.11, '10030102', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06066500' AND s.name = '06066500' AND s.agency = 'USGS';

-- Smith River bl Eagle Cr nr Fort Logan MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06077200', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06077200' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06077200', 'below Eagle Creek near Fort Logan', 46.8279805555556, -111.192238888889, '06077200', '06077200', 'Smith', 'Smith below Eagle Creek near Fort Logan', 'smith|9|005650|001087', 1087.0, 4350.0, '100301030705', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06077200' AND s.name = '06077200' AND s.agency = 'USGS';

-- Smith River near Eden MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06077500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06077500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06077500', 'near Eden', 47.1892916666667, -111.386238888889, '06077500', '06077500', 'Smith', 'Smith near Eden', 'smith|9|006480|001588', 1588.0, 3520.0, '100301030906', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06077500' AND s.name = '06077500' AND s.agency = 'USGS';

-- Sun River at Simms MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '06085800', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '06085800' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('06085800', 'at Simms', 47.50162777777778, -111.9319138888889, '06085800', '06085800', 'Sun', 'Sun at Simms', 'sun|9|006449|001296', 1296.0, 3551.08, '100301040708', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '06085800' AND s.name = '06085800' AND s.agency = 'USGS';

