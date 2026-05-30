-- Migration 0069: wire USACE outflow + water temperature onto the John Day and
-- Bonneville Columbia gauges.
--
-- The lower-Columbia USACE dams publish live outflow + water temperature on the
-- same nwd-wc.usace.army.mil getjson endpoint the repo already reads for the
-- Willamette dams (usace.cda parser). This adds each as a SECOND source on an
-- existing gauge:
--   JDA -> gauge '454249120423500' (Below John Day Dam): gains FLOW (the gauge
--          carried USGS water temp only) plus a second water-temp source.
--   BON -> gauge 'Bonneville_merge' (14128870): gains FLOW (the gauge carried
--          USGS stage + Cascade Island temp) plus a second water-temp source.
-- The Dalles is intentionally NOT wired -- its USGS gauge 14105700 already
-- serves flow + stage + temperature.
--
-- DEPLOY ORDER: the companion parser change (usace.cda units-aware, kcfs->cfs,
-- Temp-Water->temperature) must be deployed FIRST, or Flow-Out (kcfs on these
-- dams) lands 1000x small and Temp-Water is dropped.
--
-- John Day dual-key quirk: outflow is keyed 'JDA' but the water-temp series is
-- keyed 'JDY' (a sister sensor -- there is no JDA.Temp-Water). The single 'JDA'
-- source captures both: the JDY record misses source_map and falls through to
-- source_id, which fetch.py sets for a single-source fetch_url. Bonneville keys
-- both Flow-Out and Temp-Water under 'BON'.
--
-- Multi-source aggregation: update-gauge-cache surfaces the most-recent
-- observation per data_type across a gauge's sources, so each gauge's water
-- temp now reflects whichever of USGS/USACE reported last (both valid readings).
--
-- Idempotent (re-runnable): fetch_url.url is UNIQUE -> INSERT OR IGNORE;
-- source.name is intentionally non-unique (models.py::Source) -> NOT EXISTS
-- guard on (name, fetch_url_id); gauge_source has a composite PK -> INSERT OR
-- IGNORE. Rows link by name/URL, so autoincrement ids are fine. The only colon
-- is the https:// scheme (not a :word bind marker), so no @no_transaction.
--
-- The two new source names (JDA, BON) are in PENDING_RECONCILIATION
-- (tests/test_scripts/test_migration_csv_reconciliation.py) until the nightly
-- snapshot lands them in data/db/source.csv; a follow-up PR drops them.

-- John Day Dam: JDA outflow (kcfs) + JDY water temperature
INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'usace.cda',
    'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22JDA.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22,%22JDY.Temp-Water.Inst.1Hour.0.GOES-REV%22%5D&timezone=GMT&backward=2d&forward=0d',
    '',
    1
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'JDA', 'USACE', fu.id, NULL, NULL
FROM fetch_url fu
WHERE fu.url = 'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22JDA.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22,%22JDY.Temp-Water.Inst.1Hour.0.GOES-REV%22%5D&timezone=GMT&backward=2d&forward=0d'
  AND NOT EXISTS (
      SELECT 1 FROM source s WHERE s.name = 'JDA' AND s.fetch_url_id = fu.id
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '454249120423500' AND s.name = 'JDA';

-- Bonneville Dam: BON outflow (kcfs) + BON water temperature
INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'usace.cda',
    'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22BON.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22,%22BON.Temp-Water.Inst.1Hour.0.GOES-REV%22%5D&timezone=GMT&backward=2d&forward=0d',
    '',
    1
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'BON', 'USACE', fu.id, NULL, NULL
FROM fetch_url fu
WHERE fu.url = 'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22BON.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22,%22BON.Temp-Water.Inst.1Hour.0.GOES-REV%22%5D&timezone=GMT&backward=2d&forward=0d'
  AND NOT EXISTS (
      SELECT 1 FROM source s WHERE s.name = 'BON' AND s.fetch_url_id = fu.id
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Bonneville_merge' AND s.name = 'BON';
