-- Migration 0067: wire WA Lewis-system reaches + 2 new USGS gauges (Batch B).
--
-- Adds 12 paddleable reaches in the Lewis River basin (HUC 17080002):
--   NF Lewis  §1-5 (aw_3531, 3495, 5711, 2151, 2152) on USGS 14216000 (new gauge 216)
--   EF Lewis  §1-5 (aw_2149, 2147, 2150, 2148, 3530) on USGS 14222500 (gauge 53, existing)
--   Canyon Ck §1-2 (aw_2073, 3066)                  on USGS 14219000 (new gauge 217)
--
-- Names: river='Lewis' for both NF and EF reaches (display_name 'NF Lewis' / 'EF Lewis'
-- discriminates); 'Canyon Creek' for the Lewis-trib Canyon reaches (river+display_name)
-- which clusters them in the table with the existing Canyon Creek reach 179 ('Canyon ad').
-- sort_name uses the Sandy-basin convention: 'Lewis a NN ...' (NF), 'Lewis b NN ...' (EF),
-- 'Canyon ae NN ...' (sorts right after 'Canyon ad'). Section numbering = upstream→
-- downstream by put-in elevation (verified via 3DEP).
--
-- reach.geom + reach.gradient_profile come via the documented JSON exception
-- (CLAUDE.md / R6.1): the dev-only DEM/NHD/trace stack isn't on prod, so each is excluded
-- from reach.csv and lands via data/db/reaches.json + reaches-gradient.json (applied by
-- scripts/import_metadata.py). Reach.geom traces here were built by NHD HR DnHydroSeq
-- from user-refined endpoints, with manual main-channel waypoint splice on EF §5 (braided
-- lower river) and DEM channel-min snap on the 4 canyon-shaped reaches: NF §1-3, Canyon §1.
--
-- reach_guidebook entries point to Bennett's 'A Guide to the Whitewater Rivers of Washington'
-- 2nd ed (guidebook resolved by title); 11 of the 12 reaches have a Bennett entry (the
-- exception is NF Lewis §3, which Bennett doesn't index).
--
-- R4.4: the 2 new USGS source names belong in PENDING_RECONCILIATION
-- (tests/test_scripts/test_migration_csv_reconciliation.py) until the nightly snapshot lands
-- them in data/db/source.csv. Same pattern as 0065 and 0066.
--
-- Idempotent: gauge.name UNIQUE -> INSERT OR IGNORE; source.name non-unique -> NOT EXISTS
-- guard on (name, agency); reach.name UNIQUE -> INSERT OR IGNORE; reach_state composite PK
-- -> INSERT OR IGNORE; reach_class no constraint -> NOT EXISTS guard on (reach_id, cls name);
-- reach_guidebook composite PK -> INSERT OR IGNORE. Linked by name throughout.

-- gauge 14216000 (North Fork Lewis above Muddy River)
INSERT OR IGNORE INTO gauge
    (name, usgs_id, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('14216000', '14216000', 46.060355, -121.9845094,
     'Lewis, N. Fork', 'above Muddy River', 'North Fork Lewis above Muddy River',
     'lewis|0north|005000|000000', 'WA', '17080002', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '14216000', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '14216000' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '14216000' AND s.name = '14216000' AND s.agency = 'USGS';

-- gauge 14219000 (Canyon Creek near Amboy)
INSERT OR IGNORE INTO gauge
    (name, usgs_id, latitude, longitude, river, location, display_name,
     sort_name, state, huc, allow_negative_flow)
VALUES
    ('14219000', '14219000', 45.9398339, -122.3170398,
     'Canyon Creek (Lewis River trib.)', 'near Amboy', 'Canyon Creek near Amboy',
     'lewis|0canyon|005000|000000', 'WA', '17080002', 0);
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '14219000', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '14219000' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '14219000' AND s.name = '14219000' AND s.agency = 'USGS';

-- 12 reaches in INSERTION ORDER to match prod auto-increment ids (408..419 — aligns
-- with reaches.json + reaches-gradient.json which are id-keyed).
-- aw_3531  reach id 408  -- Lewis a 01 Twin Falls to FR 88
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_3531', 'NF Lewis', 'Lewis a 01 Twin Falls to FR 88', 'Lewis',
    g.id, 'Twin Falls to FR 88', 'II-III',
    'Lewis', 4.127749870766029, 83.7, 114.4,
    2652, 321,
    46.21486094346499, -121.66801578410308,
    46.19640959341199, -121.72925618344003,
    46.20563526843849, -121.69863598377155,
    3531, NULL, 0
FROM gauge g WHERE g.name = '14216000';

-- aw_3495  reach id 409  -- Lewis a 02 FR 88 to Quartz Creek
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_3495', 'NF Lewis', 'Lewis a 02 FR 88 to Quartz Creek', 'Lewis',
    g.id, 'FR 88 to Quartz Creek', 'IV(V)',
    'Lewis', 7.166447947858812, 81.9, 176.2,
    2330, 601,
    46.19640959341199, -121.72925618344003,
    46.17945325489507, -121.84676665051347,
    46.18793142415353, -121.78801141697676,
    3495, NULL, 0
FROM gauge g WHERE g.name = '14216000';

-- aw_5711  reach id 410  -- Lewis a 03 Quartz Creek to Cussed Hollow
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_5711', 'NF Lewis', 'Lewis a 03 Quartz Creek to Cussed Hollow', 'Lewis',
    g.id, 'Quartz Creek to Cussed Hollow', 'V+',
    'Lewis', 3.9554290067400655, 77.3, 140.2,
    1729, 314,
    46.17945325489507, -121.84676665051347,
    46.14437244684149, -121.89553866310538,
    46.161912850868276, -121.87115265680943,
    5711, NULL, 0
FROM gauge g WHERE g.name = '14216000';

-- aw_2151  reach id 411  -- Lewis a 04 Cussed Hollow to FR 9039
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_2151', 'NF Lewis', 'Lewis a 04 Cussed Hollow to FR 9039', 'Lewis',
    g.id, 'Cussed Hollow to FR 9039', 'III-IV',
    'Lewis', 8.272803066635902, 37.1, 65.2,
    1415, 307,
    46.14437244684149, -121.89553866310538,
    46.06209974912408, -121.9666341723693,
    46.10323609798279, -121.93108641773733,
    2151, NULL, 0
FROM gauge g WHERE g.name = '14216000';

-- aw_2152  reach id 412  -- Lewis a 05 FR 9039 to Swift Resv
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_2152', 'NF Lewis', 'Lewis a 05 FR 9039 to Swift Resv', 'Lewis',
    g.id, 'FR 9039 to Swift Resv', 'II-III',
    'Lewis', 3.751965973238022, 27.7, 27.1,
    1109, 104,
    46.06209974912408, -121.9666341723693,
    46.06564227989721, -122.02014216013376,
    46.063871014510646, -121.99338816625152,
    2152, NULL, 0
FROM gauge g WHERE g.name = '14216000';

-- aw_2149  reach id 413  -- Lewis b 01 Green Fork to Sunset Falls
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_2149', 'EF Lewis', 'Lewis b 01 Green Fork to Sunset Falls', 'Lewis',
    g.id, 'Green Fork to Sunset Falls', 'III-IV(V)',
    'Lewis', 5.362491194760441, 109.8, 173.5,
    1566, 589,
    45.82291866281684, -122.16455790254149,
    45.81764938991045, -122.25274251100815,
    45.820284026363645, -122.20865020677482,
    2149, NULL, 0
FROM gauge g WHERE g.name = 'EF_Lewis_Washington_merge';

-- aw_2147  reach id 414  -- Lewis b 02 Sunset Falls to Horseshoe Falls
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_2147', 'EF Lewis', 'Lewis b 02 Sunset Falls to Horseshoe Falls', 'Lewis',
    g.id, 'Sunset Falls to Horseshoe Falls', 'IV',
    'Lewis', 3.8040676876176525, 66.0, 88.9,
    977, 251,
    45.81764938991045, -122.25274251100815,
    45.81454377126033, -122.32450146604936,
    45.816096580585395, -122.28862198852875,
    2147, NULL, 0
FROM gauge g WHERE g.name = 'EF_Lewis_Washington_merge';

-- aw_2150  reach id 415  -- Lewis b 03 Rock Creek to Moulton Falls
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_2150', 'EF Lewis', 'Lewis b 03 Rock Creek to Moulton Falls', 'Lewis',
    g.id, 'Rock Creek to Moulton Falls', 'III-V',
    'Lewis', 1.7825258310397525, 53.3, 57.4,
    612, 95,
    45.814750059931576, -122.36795749513439,
    45.83150650515193, -122.39008184333217,
    45.82312828254175, -122.37901966923329,
    2150, NULL, 0
FROM gauge g WHERE g.name = 'EF_Lewis_Washington_merge';

-- aw_2148  reach id 416  -- Lewis b 04 Moulton Falls to Lewisville Park
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_2148', 'EF Lewis', 'Lewis b 04 Moulton Falls to Lewisville Park', 'Lewis',
    g.id, 'Moulton Falls to Lewisville Park', 'III+(V)',
    'Lewis', 10.015403273990177, 35.4, 61.3,
    521, 355,
    45.831361463316455, -122.38912294784332,
    45.82275202437011, -122.53399713660596,
    45.827056743843286, -122.46156004222465,
    2148, NULL, 0
FROM gauge g WHERE g.name = 'EF_Lewis_Washington_merge';

-- aw_3530  reach id 417  -- Lewis b 05 Lewisville Park to Daybreak Park
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_3530', 'EF Lewis', 'Lewis b 05 Lewisville Park to Daybreak Park', 'Lewis',
    g.id, 'Lewisville Park to Daybreak Park', 'II',
    'Lewis', 3.8411931095898546, 20.8, 32.8,
    166, 85,
    45.82275202437011, -122.53399713660596,
    45.814634, -122.589108,
    45.81869301218505, -122.56155256830297,
    3530, NULL, 0
FROM gauge g WHERE g.name = 'EF_Lewis_Washington_merge';

-- aw_2073  reach id 418  -- Canyon ae 01 Twin Bridges to Fly Creek
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_2073', 'Canyon Creek', 'Canyon ae 01 Twin Bridges to Fly Creek', 'Canyon Creek',
    g.id, 'Twin Bridges to Fly Creek', 'IV-V',
    'Lewis', 8.595532833222025, 98.8, 154.1,
    1406, 907,
    45.90745071616666, -122.18612869486408,
    45.940055928208714, -122.31642110987625,
    45.923753322187686, -122.25127490237017,
    2073, NULL, 0
FROM gauge g WHERE g.name = '14219000';

-- aw_3066  reach id 419  -- Canyon ae 02 Fly Creek to Merwin Resv
INSERT OR IGNORE INTO reach
    (name, display_name, sort_name, river, gauge_id, description, difficulties,
     basin, length, gradient, max_gradient, elevation, elevation_lost,
     latitude_start, longitude_start, latitude_end, longitude_end,
     latitude, longitude, aw_id, huc, no_show)
SELECT
    'aw_3066', 'Canyon Creek', 'Canyon ae 02 Fly Creek to Merwin Resv', 'Canyon Creek',
    g.id, 'Fly Creek to Merwin Resv', 'IV+',
    'Lewis', 4.3753693502541156, 59.9, 170.5,
    499, 262,
    45.940055928208714, -122.31642110987625,
    45.96060252165227, -122.37251988537328,
    45.95032922493049, -122.34447049762477,
    3066, NULL, 0
FROM gauge g WHERE g.name = '14219000';

-- reach_state: all 12 in Washington
INSERT OR IGNORE INTO reach_state (reach_id, state_id)
SELECT r.id, st.id FROM reach r, state st
WHERE r.name IN ('aw_3531','aw_3495','aw_5711','aw_2151','aw_2152','aw_2149','aw_2147','aw_2150','aw_2148','aw_3530','aw_2073','aw_3066') AND st.abbreviation = 'WA';

-- reach_class: difficulty + low/high flow ranges per AW
INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'II-III', 500.0, 'flow', 15000.0, 'flow'
FROM reach r WHERE r.name = 'aw_3531'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'II-III');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'IV(V)', 700.0, 'flow', 2000.0, 'flow'
FROM reach r WHERE r.name = 'aw_3495'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'IV(V)');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'V+', 300.0, 'flow', 3000.0, 'flow'
FROM reach r WHERE r.name = 'aw_5711'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'V+');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'III-IV', 700.0, 'flow', 2000.0, 'flow'
FROM reach r WHERE r.name = 'aw_2151'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'III-IV');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'II-III', 700.0, 'flow', 3000.0, 'flow'
FROM reach r WHERE r.name = 'aw_2152'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'II-III');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'III-IV(V)', 1000.0, 'flow', 3000.0, 'flow'
FROM reach r WHERE r.name = 'aw_2149'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'III-IV(V)');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'IV', 400.0, 'flow', 2800.0, 'flow'
FROM reach r WHERE r.name = 'aw_2147'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'IV');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'III-V', 1500.0, 'flow', 3000.0, 'flow'
FROM reach r WHERE r.name = 'aw_2150'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'III-V');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'III+(V)', 500.0, 'flow', 2500.0, 'flow'
FROM reach r WHERE r.name = 'aw_2148'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'III+(V)');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'II', 600.0, 'flow', 1200.0, 'flow'
FROM reach r WHERE r.name = 'aw_3530'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'II');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'IV-V', 400.0, 'flow', 2000.0, 'flow'
FROM reach r WHERE r.name = 'aw_2073'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'IV-V');

INSERT INTO reach_class (reach_id, name, low, low_data_type, high, high_data_type)
SELECT r.id, 'IV+', 250.0, 'flow', 1500.0, 'flow'
FROM reach r WHERE r.name = 'aw_3066'
  AND NOT EXISTS (SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'IV+');

-- reach_guidebook: Bennett's WA whitewater guide (2nd ed) page+run per reach (11 of 12)
INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '315', '295'
FROM reach r, guidebook g WHERE r.name = 'aw_3531'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '51', '26'
FROM reach r, guidebook g WHERE r.name = 'aw_3495'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '52', '27'
FROM reach r, guidebook g WHERE r.name = 'aw_2151'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '53', '28'
FROM reach r, guidebook g WHERE r.name = 'aw_2152'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '46', '22'
FROM reach r, guidebook g WHERE r.name = 'aw_2149'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '47', '23'
FROM reach r, guidebook g WHERE r.name = 'aw_2147'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '49', '24'
FROM reach r, guidebook g WHERE r.name = 'aw_2150'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '50', '25'
FROM reach r, guidebook g WHERE r.name = 'aw_2148'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '314', '294'
FROM reach r, guidebook g WHERE r.name = 'aw_3530'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '55', '30'
FROM reach r, guidebook g WHERE r.name = 'aw_2073'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, page, run)
SELECT r.id, g.id, '55', '31'
FROM reach r, guidebook g WHERE r.name = 'aw_3066'
  AND g.title = 'A Guide to the Whitewater Rivers of Washington' AND g.edition = '2nd' AND g.author = 'Jeff and Tonya Bennet';

