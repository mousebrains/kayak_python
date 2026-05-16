-- Migration 0024: wire source 43 (IRGC1) to its NWPS feed.
--
-- IRGC1 = Klamath River below Iron Gate Dam (CA), CNRFC forecast point,
-- USGS 11516530, on gauge 45 Klamath_Iron_Gate_Merge. The source row
-- existed with fetch_url_id=NULL — likely a holdover from the legacy
-- MySQL sync era — so nothing was fetching it directly. The USGS
-- sibling (source 245) kept the gauge alive at the cache level, but
-- IRGC1 itself only ever got data when `levels merge` ran manually
-- (last 2026-05-06, before merge was retired in c5bac0f); after that
-- it surfaced as a stale NOAA source on status.php.
--
-- NWPS /nwps/v1/gauges/IRGC1 returns live observed stage + flow
-- (verified 2026-05-15 23:15Z). Wiring source 43 to that URL gives it
-- a real fetcher; the existing source name "IRGC1" already matches
-- what the nwps parser emits, so the parser's source_map lookup will
-- resolve correctly once fetch_url_id is set — no duplicate row will
-- be auto-created.

INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'nwps',
    'https://api.water.noaa.gov/nwps/v1/gauges/IRGC1/stageflow/observed',
    '',
    1
);

UPDATE source SET fetch_url_id = (
    SELECT id FROM fetch_url
    WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/IRGC1/stageflow/observed'
)
WHERE id = 43;
