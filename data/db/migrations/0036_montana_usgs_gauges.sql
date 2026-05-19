-- Migration 0036: Montana USGS gauges in HUC4 1701 (Pacific drainage).
--
-- Pulled from data/discover/montana_candidates.csv (7-day-active sites,
-- HUC4 1701 ∩ state=MT). See docs/PLAN_montana_gauges.md.
--
-- Idempotent: re-running is safe (INSERT OR IGNORE / WHERE NOT EXISTS).

-- Tobacco River at Eureka, MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12301250', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12301250' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12301250', 'at Eureka', 48.8779472222222, -115.054461111111, '12301250', '12301250', 'Tobacco', 'Tobacco at Eureka', 'tobacco|9|007445|000396', 396.2, 2555.12, '170101010806', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12301250' AND s.name = '12301250' AND s.agency = 'USGS';

-- Kootenai River bl Libby Dam nr Libby MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12301933', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12301933' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12301933', 'below Libby Dam near Libby', 48.4006638888889, -115.318719444444, '12301933', '12301933', 'Kootenai', 'Kootenai below Libby Dam near Libby', 'kootenai|9|007900|008999', 8999.0, 2100.0, '170101011211', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12301933' AND s.name = '12301933' AND s.agency = 'USGS';

-- Libby Wetland Site bl Schrieber Lake nr Libby, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '480608115242901', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '480608115242901' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('480608115242901', 'below Schrieber Lake near Libby', 48.1022258986402, -115.40891878949, '480608115242901', '480608115242901', 'Libby Wetland Site', 'Libby Wetland Site below Schrieber Lake near Libby', 'libbywetlandsite|9|006980|999999', NULL, 3020.0, '170101020402', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '480608115242901' AND s.name = '480608115242901' AND s.agency = 'USGS';

-- Fisher River near Libby MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12302055', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12302055' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12302055', 'near Libby', 48.3556027777778, -115.31465, '12302055', '12302055', 'Fisher', 'Fisher near Libby', 'fisher|9|007866|000842', 842.0, 2134.1, '170101020406', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12302055' AND s.name = '12302055' AND s.agency = 'USGS';

-- Yaak River near Troy MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12304500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12304500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12304500', 'near Troy', 48.5617222222222, -115.970158333333, '12304500', '12304500', 'Yaak', 'Yaak near Troy', 'yaak|9|008161|000792', 792.0, 1839.0, '170101030505', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12304500' AND s.name = '12304500' AND s.agency = 'USGS';

-- Blacktail Creek above Grove Gulch, at Butte, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323233', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323233' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323233', 'above Grove Gulch, at Butte', 45.9912694444444, -112.527358333333, '12323233', '12323233', 'Blacktail Creek', 'Blacktail Creek above Grove Gulch, at Butte', 'blacktailcreek|9|004543|000090', 90.1, 5457.0, '170102010204', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323233' AND s.name = '12323233' AND s.agency = 'USGS';

-- Silver Bow Creek at Montana Street, at Butte, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323242', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323242' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323242', 'at Montana Street, at Butte', 45.9957277777778, -112.538761111111, '12323242', '12323242', 'Silver Bow Creek', 'Silver Bow Creek at Montana Street, at Butte', 'silverbowcreek|9|004551|000116', 116.0, 5449.0, '170102010204', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323242' AND s.name = '12323242' AND s.agency = 'USGS';

-- Silver Bow Cr bl Blacktail Cr, at Butte, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323250', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323250' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323250', 'below Blacktail Creek, at Butte', 45.9991666666667, -112.577322222222, '12323250', '12323250', 'Silver Bow Creek', 'Silver Bow Creek below Blacktail Creek, at Butte', 'silverbowcreek|9|004580|000125', 124.75, 5420.0, '170102010204', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323250' AND s.name = '12323250' AND s.agency = 'USGS';

-- Willow Creek nr Anaconda, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323710', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323710' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323710', 'near Anaconda', 46.0645111111111, -112.893530555556, '12323710', '12323710', 'Willow Creek', 'Willow Creek near Anaconda', 'willowcreek|9|004810|000014', 13.7, 5190.0, '170102010207', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323710' AND s.name = '12323710' AND s.agency = 'USGS';

-- Willow Creek at Opportunity, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323720', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323720' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323720', 'at Opportunity', 46.1071638888889, -112.810611111111, '12323720', '12323720', 'Willow Creek', 'Willow Creek at Opportunity', 'willowcreek|9|005070|000029', 28.6, 4930.0, '170102010207', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323720' AND s.name = '12323720' AND s.agency = 'USGS';

-- Mill Creek nr Anaconda, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323670', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323670' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323670', 'near Anaconda', 46.0829722222222, -112.917188888889, '12323670', '12323670', 'Mill Creek', 'Mill Creek near Anaconda', 'millcreek|9|004530|000040', 39.5, 5470.0, '170102010208', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323670' AND s.name = '12323670' AND s.agency = 'USGS';

-- Mill Creek at Opportunity, MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323700', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323700' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323700', 'at Opportunity', 46.1136027777778, -112.828061111111, '12323700', '12323700', 'Mill Creek', 'Mill Creek at Opportunity', 'millcreek|9|005035|000042', 42.4, 4965.0, '170102010208', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323700' AND s.name = '12323700' AND s.agency = 'USGS';

-- Silver Bow Creek at Opportunity MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323600', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323600' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323600', 'at Opportunity', 46.1077583333333, -112.805283333333, '12323600', '12323600', 'Silver Bow Creek', 'Silver Bow Creek at Opportunity', 'silverbowcreek|9|005088|000343', 343.0, 4912.0, '170102010209', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323600' AND s.name = '12323600' AND s.agency = 'USGS';

-- Warm Springs Creek near Anaconda MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323760', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323760' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323760', 'near Anaconda', 46.1336666666667, -112.903152777778, '12323760', '12323760', 'Warm Springs Creek', 'Warm Springs Creek near Anaconda', 'warmspringscreek|9|004850|000156', 156.0, 5150.0, '170102010305', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323760' AND s.name = '12323760' AND s.agency = 'USGS';

-- Silver Bow Creek at Warm Springs MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323750', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323750' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323750', 'at Warm Springs', 46.1794972222222, -112.780561111111, '12323750', '12323750', 'Silver Bow Creek', 'Silver Bow Creek at Warm Springs', 'silverbowcreek|9|005212|000473', 473.0, 4787.95, '170102010401', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323750' AND s.name = '12323750' AND s.agency = 'USGS';

-- Warm Springs Creek at Warm Springs MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323770', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323770' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323770', 'at Warm Springs', 46.180375, -112.785077777778, '12323770', '12323770', 'Warm Springs Creek', 'Warm Springs Creek at Warm Springs', 'warmspringscreek|9|005190|000160', 160.0, 4810.0, '170102010401', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323770' AND s.name = '12323770' AND s.agency = 'USGS';

-- Clark Fork near Galen MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323800', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323800' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323800', 'near Galen', 46.2082416666667, -112.76735, '12323800', '12323800', 'Clark Fork', 'Clark Fork near Galen', 'clarkfork|9|005251|000656', 656.0, 4749.0, '170102010401', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323800' AND s.name = '12323800' AND s.agency = 'USGS';

-- Lost Creek near Anaconda MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323840', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323840' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323840', 'near Anaconda', 46.161325, -112.893797222222, '12323840', '12323840', 'Lost Creek', 'Lost Creek near Anaconda', 'lostcreek|9|004900|000026', 26.5, 5100.0, '170102010402', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323840' AND s.name = '12323840' AND s.agency = 'USGS';

-- Lost Creek near Galen, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12323850', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12323850' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12323850', 'near Galen', 46.2185722222222, -112.774166666667, '12323850', '12323850', 'Lost Creek', 'Lost Creek near Galen', 'lostcreek|9|005250|000060', 60.5, 4750.0, '170102010403', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12323850' AND s.name = '12323850' AND s.agency = 'USGS';

-- Little Blackfoot River near Garrison MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12324590', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12324590' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12324590', 'near Garrison', 46.5194833333333, -112.793172222222, '12324590', '12324590', 'Little Blackfoot', 'Little Blackfoot near Garrison', 'littleblackfoot|9|005652|000414', 414.0, 4347.73, '170102010611', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12324590' AND s.name = '12324590' AND s.agency = 'USGS';

-- Clark Fork at Deer Lodge MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12324200', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12324200' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12324200', 'at Deer Lodge', 46.39765, -112.742538888889, '12324200', '12324200', 'Clark Fork', 'Clark Fork at Deer Lodge', 'clarkfork|9|005498|001001', 1001.0, 4502.2, '170102010707', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12324200' AND s.name = '12324200' AND s.agency = 'USGS';

-- Clark Fork ab Little Blackfoot R nr Garrison MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12324400', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12324400' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12324400', 'above Little Blackfoot R near Garrison', 46.5109111111111, -112.789686111111, '12324400', '12324400', 'Clark Fork', 'Clark Fork above Little Blackfoot R near Garrison', 'clarkfork|9|005650|001130', 1130.0, 4350.0, '170102010708', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12324400' AND s.name = '12324400' AND s.agency = 'USGS';

-- Clark Fork at Goldcreek MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12324680', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12324680' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12324680', 'at Goldcreek', 46.5899888888889, -112.928713888889, '12324680', '12324680', 'Clark Fork', 'Clark Fork at Goldcreek', 'clarkfork|9|005827|001774', 1774.0, 4172.8, '170102010807', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12324680' AND s.name = '12324680' AND s.agency = 'USGS';

-- Flint Creek near Southern Cross MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12325500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12325500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12325500', 'near Southern Cross', 46.2326666666667, -113.299872222222, '12325500', '12325500', 'Flint Creek', 'Flint Creek near Southern Cross', 'flintcreek|9|004370|000054', 54.0, 5630.0, '170102020105', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12325500' AND s.name = '12325500' AND s.agency = 'USGS';

-- Flint Creek at Maxville MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12329500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12329500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12329500', 'at Maxville', 46.4637583333333, -113.240277777778, '12329500', '12329500', 'Flint Creek', 'Flint Creek at Maxville', 'flintcreek|9|005172|000206', 206.0, 4828.38, '170102020204', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12329500' AND s.name = '12329500' AND s.agency = 'USGS';

-- Boulder Creek at Maxville MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12330000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12330000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12330000', 'at Maxville', 46.4716527777778, -113.235769444444, '12330000', '12330000', 'Boulder Creek', 'Boulder Creek at Maxville', 'bouldercreek|9|005250|000070', 70.5, 4750.0, '170102020303', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12330000' AND s.name = '12330000' AND s.agency = 'USGS';

-- Flint Creek near Drummond MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12331500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12331500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12331500', 'near Drummond', 46.6287277777778, -113.150691666667, '12331500', '12331500', 'Flint Creek', 'Flint Creek near Drummond', 'flintcreek|9|005983|000490', 490.0, 4017.27, '170102020506', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12331500' AND s.name = '12331500' AND s.agency = 'USGS';

-- Clark Fork near Drummond MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12331800', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12331800' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12331800', 'near Drummond', 46.7119138888889, -113.330780555556, '12331800', '12331800', 'Clark Fork', 'Clark Fork near Drummond', 'clarkfork|9|006190|002516', 2516.0, 3810.0, '170102020605', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12331800' AND s.name = '12331800' AND s.agency = 'USGS';

-- Middle Fork Rock Cr nr Philipsburg MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12332000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12332000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12332000', 'near Philipsburg', 46.1845694444444, -113.501569444444, '12332000', '12332000', 'Middle Fork Rock Creek', 'Middle Fork Rock Creek near Philipsburg', 'middleforkrockcreek|9|004556|000121', 121.0, 5444.08, '170102020805', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12332000' AND s.name = '12332000' AND s.agency = 'USGS';

-- Rock Creek near Clinton MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12334510', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12334510' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12334510', 'near Clinton', 46.7223361111111, -113.683061111111, '12334510', '12334510', 'Rock Creek', 'Rock Creek near Clinton', 'rockcreek|9|006481|000889', 889.0, 3519.46, '170102021306', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12334510' AND s.name = '12334510' AND s.agency = 'USGS';

-- Clark Fork at Turah Bridge nr Bonner MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12334550', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12334550' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12334550', 'at Turah Bridge near Bonner', 46.8259111111111, -113.814030555556, '12334550', '12334550', 'Clark Fork', 'Clark Fork at Turah Bridge near Bonner', 'clarkfork|9|006680|003657', 3657.0, 3320.0, '170102021405', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12334550' AND s.name = '12334550' AND s.agency = 'USGS';

-- NF Blackfoot R ab Dry Gulch nr Ovando MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12338300', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12338300' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12338300', 'above Dry Gulch near Ovando', 46.9794944444444, -113.092186111111, '12338300', '12338300', 'North Fork Blackfoot R', 'North Fork Blackfoot R above Dry Gulch near Ovando', 'northforkblackfootr|9|005929|000310', 310.0, 4070.84, '170102030706', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12338300' AND s.name = '12338300' AND s.agency = 'USGS';

-- Blackfoot R ab Nevada Cr nr Helmville MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12335100', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12335100' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12335100', 'above Nevada Creek near Helmville', 46.9187944444444, -113.014986111111, '12335100', '12335100', 'Blackfoot R', 'Blackfoot R above Nevada Creek near Helmville', 'blackfootr|9|005745|000498', 498.0, 4255.36, '170102030903', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12335100' AND s.name = '12335100' AND s.agency = 'USGS';

-- Blackfoot River near Bonner MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12340000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12340000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12340000', 'near Bonner', 46.8994111111111, -113.756319444444, '12340000', '12340000', 'Blackfoot', 'Blackfoot near Bonner', 'blackfoot|9|006655|002287', 2287.0, 3344.76, '170102031308', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12340000' AND s.name = '12340000' AND s.agency = 'USGS';

-- Clark Fork above Missoula MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12340500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12340500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12340500', 'above Missoula', 46.8767638888889, -113.932119444444, '12340500', '12340500', 'Clark Fork', 'Clark Fork above Missoula', 'clarkfork|9|006802|006021', 6021.0, 3198.3, '170102040104', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12340500' AND s.name = '12340500' AND s.agency = 'USGS';

-- Clark Fork below Missoula MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12353000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12353000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12353000', 'below Missoula', 46.8686333333333, -114.127747222222, '12353000', '12353000', 'Clark Fork', 'Clark Fork below Missoula', 'clarkfork|9|006916|009017', 9017.0, 3083.88, '170102040205', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12353000' AND s.name = '12353000' AND s.agency = 'USGS';

-- Clark Fork at Superior, MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12353650', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12353650' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12353650', 'at Superior', 47.196389, -114.889444, '12353650', '12353650', 'Clark Fork', 'Clark Fork at Superior', 'clarkfork|9|999999|010210', 10210.0, NULL, '170102040612', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12353650' AND s.name = '12353650' AND s.agency = 'USGS';

-- St. Regis River near St. Regis, MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12354000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12354000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12354000', 'near St. Regis', 47.2967111111111, -115.122625, '12354000', '12354000', 'St. Regis', 'St. Regis near St. Regis', 'stregis|9|007355|000304', 304.0, 2645.0, '170102040712', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12354000' AND s.name = '12354000' AND s.agency = 'USGS';

-- Clark Fork at St. Regis MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12354500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12354500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12354500', 'at St. Regis', 47.3016388888889, -115.086869444444, '12354500', '12354500', 'Clark Fork', 'Clark Fork at St. Regis', 'clarkfork|9|007400|010728', 10728.0, 2600.37, '170102040805', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12354500' AND s.name = '12354500' AND s.agency = 'USGS';

-- Clark Fork near Paradise, MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12354700', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12354700' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12354700', 'near Paradise', 47.321944, -114.89, '12354700', '12354700', 'Clark Fork', 'Clark Fork near Paradise', 'clarkfork|9|999999|010709', 10709.0, NULL, '170102040807', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12354700' AND s.name = '12354700' AND s.agency = 'USGS';

-- West Fork Bitterroot River nr Conner MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12342500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12342500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12342500', 'near Conner', 45.7248277777778, -114.282294444444, '12342500', '12342500', 'West Fork Bitterroot', 'West Fork Bitterroot near Conner', 'westforkbitterroot|9|005419|000317', 317.0, 4581.4, '170102050301', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12342500' AND s.name = '12342500' AND s.agency = 'USGS';

-- Lake Como Spillway near Darby, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12344501', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12344501' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12344501', 'near Darby', 46.0679611111111, -114.238758333333, '12344501', '12344501', 'Lake Como Spillway', 'Lake Como Spillway near Darby', 'lakecomospillway|9|005759|999999', NULL, 4241.0, '170102050805', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12344501' AND s.name = '12344501' AND s.agency = 'USGS';

-- Rock Cr bl canal diversion, near Darby, MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12345510', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12345510' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12345510', 'below canal diversion, near Darby', 46.0686138888889, -114.217991666667, '12345510', '12345510', 'Rock Creek', 'Rock Creek below canal diversion, near Darby', 'rockcreek|9|005954|000056', 55.5, 4046.0, '170102050805', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12345510' AND s.name = '12345510' AND s.agency = 'USGS';

-- Bitterroot River near Darby MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12344000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12344000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12344000', 'near Darby', 45.97205, -114.141233333333, '12344000', '12344000', 'Bitterroot', 'Bitterroot near Darby', 'bitterroot|9|006058|001050', 1050.0, 3942.14, '170102050806', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12344000' AND s.name = '12344000' AND s.agency = 'USGS';

-- Bitterroot River at Bell Crossing nr Victor MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12350250', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12350250' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12350250', 'at Bell Crossing near Victor', 46.4432, -114.123766666667, '12350250', '12350250', 'Bitterroot', 'Bitterroot at Bell Crossing near Victor', 'bitterroot|9|006670|001944', 1944.0, 3330.0, '170102051203', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12350250' AND s.name = '12350250' AND s.agency = 'USGS';

-- Bitterroot River near Missoula MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12352500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12352500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12352500', 'near Missoula', 46.8317388888889, -114.054861111111, '12352500', '12352500', 'Bitterroot', 'Bitterroot near Missoula', 'bitterroot|9|006890|002824', 2824.0, 3110.0, '170102051603', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12352500' AND s.name = '12352500' AND s.agency = 'USGS';

-- N F Flathead River nr Columbia Falls MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12355500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12355500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12355500', 'near Columbia Falls', 48.4957972222222, -114.126763888889, '12355500', '12355500', 'North Fork Flathead', 'North Fork Flathead near Columbia Falls', 'northforkflathead|9|006854|001556', 1556.0, 3145.59, '170102060607', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12355500' AND s.name = '12355500' AND s.agency = 'USGS';

-- M F Flathead River near West Glacier MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12358500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12358500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12358500', 'near West Glacier', 48.4955166666667, -114.010208333333, '12358500', '12358500', 'Middle Fork Flathead', 'Middle Fork Flathead near West Glacier', 'middleforkflathead|9|006871|001125', 1125.0, 3128.72, '170102070505', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12358500' AND s.name = '12358500' AND s.agency = 'USGS';

-- Flathead River at Columbia Falls MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12363000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12363000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12363000', 'at Columbia Falls', 48.3618111111111, -114.18495, '12363000', '12363000', 'Flathead', 'Flathead at Columbia Falls', 'flathead|9|007018|004473', 4473.0, 2981.54, '170102080203', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12363000' AND s.name = '12363000' AND s.agency = 'USGS';

-- Flathead River at Foys Bend nr Kalispell MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12366500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12366500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12366500', 'at Foys Bend near Kalispell', 48.1543111111111, -114.248925, '12366500', '12366500', 'Flathead', 'Flathead at Foys Bend near Kalispell', 'flathead|9|007099|005341', 5341.0, 2900.63, '170102080205', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12366500' AND s.name = '12366500' AND s.agency = 'USGS';

-- S F Flathead R ab Twin C nr Hungry Horse MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12359800', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12359800' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12359800', 'above Twin C near Hungry Horse', 47.9790972222222, -113.560683333333, '12359800', '12359800', 'South Fork Flathead R', 'South Fork Flathead R above Twin C near Hungry Horse', 'southforkflatheadr|9|006425|001159', 1159.0, 3575.0, '170102090509', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12359800' AND s.name = '12359800' AND s.agency = 'USGS';

-- S F Flathead River nr Columbia Falls MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12362500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12362500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12362500', 'near Columbia Falls', 48.3565777777778, -114.037858333333, '12362500', '12362500', 'South Fork Flathead', 'South Fork Flathead near Columbia Falls', 'southforkflathead|9|006960|001668', 1668.0, 3040.0, '170102090707', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12362500' AND s.name = '12362500' AND s.agency = 'USGS';

-- Stillwater River at Lawrence Park, at Kalispell
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12365700', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12365700' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12365700', 'at Lawrence Park, at Kalispell', 48.21725, -114.313736111111, '12365700', '12365700', 'Stillwater', 'Stillwater at Lawrence Park, at Kalispell', 'stillwater|9|007060|000596', 596.0, 2940.0, '170102100403', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12365700' AND s.name = '12365700' AND s.agency = 'USGS';

-- Whitefish River near Kalispell MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12366000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12366000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12366000', 'near Kalispell', 48.3201861111111, -114.278594444444, '12366000', '12366000', 'Whitefish', 'Whitefish near Kalispell', 'whitefish|9|007030|000170', 170.0, 2969.83, '170102100509', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12366000' AND s.name = '12366000' AND s.agency = 'USGS';

-- Swan River near Bigfork, MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12370000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12370000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12370000', 'near Bigfork', 48.0242305555556, -113.978819444444, '12370000', '12370000', 'Swan', 'Swan near Bigfork', 'swan|9|006937|000672', 672.0, 3062.6, '170102110402', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12370000' AND s.name = '12370000' AND s.agency = 'USGS';

-- Flathead River near Polson MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12372000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12372000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12372000', 'near Polson', 47.6802861111111, -114.246727777778, '12372000', '12372000', 'Flathead', 'Flathead near Polson', 'flathead|9|007307|007079', 7079.0, 2692.7, '170102120301', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12372000' AND s.name = '12372000' AND s.agency = 'USGS';

-- Mission Creek ab reservoir nr St. Ignatius MT -- REVIEW: industrial / monitoring site?
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12377150', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12377150' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12377150', 'above reservoir near St. Ignatius', 47.3201916666667, -113.987891666667, '12377150', '12377150', 'Mission Creek', 'Mission Creek above reservoir near St. Ignatius', 'missioncreek|9|006540|000014', 13.5, 3460.0, '170102120503', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12377150' AND s.name = '12377150' AND s.agency = 'USGS';

-- South Fork Jocko River near Arlee MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12381400', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12381400' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12381400', 'near Arlee', 47.19555, -113.850736111111, '12381400', '12381400', 'South Fork Jocko', 'South Fork Jocko near Arlee', 'southforkjocko|9|006030|000058', 57.6, 3970.0, '170102120703', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12381400' AND s.name = '12381400' AND s.agency = 'USGS';

-- Flathead River at Perma MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12388700', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12388700' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12388700', 'at Perma', 47.3667916666667, -114.585, '12388700', '12388700', 'Flathead', 'Flathead at Perma', 'flathead|9|007531|009024', 9024.0, 2469.3, '170102120809', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12388700' AND s.name = '12388700' AND s.agency = 'USGS';

-- Thompson River near Thompson Falls MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12389500', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12389500' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12389500', 'near Thompson Falls', 47.5918583333333, -115.229536111111, '12389500', '12389500', 'Thompson', 'Thompson near Thompson Falls', 'thompson|9|007570|000638', 638.0, 2429.97, '170102130407', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12389500' AND s.name = '12389500' AND s.agency = 'USGS';

-- Clark Fork near Plains MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12389000', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12389000' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12389000', 'near Plains', 47.4292, -114.856533333333, '12389000', '12389000', 'Clark Fork', 'Clark Fork near Plains', 'clarkfork|9|007551|019964', 19964.0, 2449.11, '170102130510', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12389000' AND s.name = '12389000' AND s.agency = 'USGS';

-- Prospect Creek at Thompson Falls MT
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '12390700', 'USGS', NULL, NULL, ''
WHERE NOT EXISTS (SELECT 1 FROM source WHERE name = '12390700' AND agency = 'USGS');
INSERT OR IGNORE INTO gauge (name, location, latitude, longitude, usgs_id, station_id, river, display_name, sort_name, drainage_area, elevation, huc, allow_negative_flow, state) VALUES ('12390700', 'at Thompson Falls', 47.5860611111111, -115.3551, '12390700', '12390700', 'Prospect Creek', 'Prospect Creek at Thompson Falls', 'prospectcreek|9|007618|000182', 182.0, 2382.4, '170102130607', 0, 'MT');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) SELECT g.id, s.id FROM gauge g, source s WHERE g.name = '12390700' AND s.name = '12390700' AND s.agency = 'USGS';

