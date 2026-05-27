-- Migration 0065: split USGS sources out of conflated alternative sources.
--
-- The source-based fetch-usgs-ogc (this PR) requires a USGS station's data to
-- live in a USGS source. Two gauges had a live usgs_id but only a non-USGS
-- (NWS) source, so the old gauge.usgs_id-keyed fetcher stored USGS data into
-- that NWS source -- two providers conflated in one source. Give each its own
-- USGS source so the gauge becomes [USGS] + [NWS] aggregated (the Wind pattern,
-- e.g. gauge 14128500 = USGS 14128500 + NWS WCNW1):
--
--   gauge 14147500           + NWS NMFO3 : USGS 14147500 serves 00065 gage + 00010 temp
--   gauge Blue_Tidbits_merge + NWS BRTO3 : USGS 14161100 serves 00065 gage + 00010 temp
--
-- NWPS carries no temperature, so that 00010 is the USGS contribution the NWS
-- feed cannot supply -- dropping it (no split) would lose the gauge's temp.
--
-- The other gauges that have a usgs_id but no USGS source need nothing: either
-- USGS has gone dark for them, or the station reports no mapped parameter --
-- e.g. Applegate Lake (14361900) publishes only 62614 (lake-surface elevation),
-- which the fetcher doesn't map, so it returns zero fetchable rows. The
-- source-based fetcher correctly fetches nothing for those.
--
-- Idempotent: source.name is non-unique (models.py::Source) -> NOT EXISTS guard
-- on (name, agency); gauge_source is a composite PK -> INSERT OR IGNORE. Linked
-- by name, so prod-assigned ids are fine. No colon/semicolon/-- in any literal.
--
-- R4.4: the 2 new USGS source names are in PENDING_RECONCILIATION
-- (tests/test_scripts/test_migration_csv_reconciliation.py) until the nightly
-- snapshot lands them in data/db/source.csv.

-- 1. NF ... at NMFO3 (gauge is named for its station id)
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '14147500', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '14147500' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '14147500' AND s.name = '14147500' AND s.agency = 'USGS';

-- 2. Blue_Tidbits_merge (usgs_id 14161100), alongside NWS BRTO3
INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT '14161100', 'USGS', NULL, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM source s WHERE s.name = '14161100' AND s.agency = 'USGS');
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Blue_Tidbits_merge' AND s.name = '14161100' AND s.agency = 'USGS';
