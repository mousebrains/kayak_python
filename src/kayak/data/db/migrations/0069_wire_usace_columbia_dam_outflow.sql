-- Migration 0069: wire USACE outflow onto the John Day and Bonneville Columbia
-- gauges.
--
-- The lower-Columbia USACE dams publish live outflow (discharge) on the same
-- nwd-wc.usace.army.mil getjson endpoint the repo already reads for the
-- Willamette dams (usace.cda parser). Outflow is the whole gain -- both gauges
-- currently lack a flow reading:
--   JDA -> gauge '454249120423500' (Below John Day Dam): had USGS water temp only.
--   BON -> gauge 'Bonneville_merge' (14128870): had USGS stage + temperature.
--
-- FLOW ONLY -- the USACE water-temperature series are deliberately not wired:
-- John Day's (JDY, "John Day Dam Water Quality") is an at-dam sensor ~3 mi
-- upstream of the gauge's downstream USGS temp, and Bonneville's (BON) is
-- co-located with the gauge's USGS Cascade Island temp (the two track to
-- ~0.06 F) -- so neither adds meaningfully over the USGS temperature already on
-- the gauge. The Dalles is not wired at all (USGS already serves flow+stage+temp).
--
-- DEPLOY ORDER: the companion parser change (usace.cda kcfs->cfs unit scaling)
-- must be deployed FIRST, or Flow-Out (kcfs on these dams) lands 1000x small.
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

-- John Day Dam: JDA outflow (kcfs)
INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'usace.cda',
    'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22JDA.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22%5D&timezone=GMT&backward=2d&forward=0d',
    '',
    1
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'JDA', 'USACE', fu.id, NULL, NULL
FROM fetch_url fu
WHERE fu.url = 'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22JDA.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22%5D&timezone=GMT&backward=2d&forward=0d'
  AND NOT EXISTS (
      SELECT 1 FROM source s WHERE s.name = 'JDA' AND s.fetch_url_id = fu.id
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = '454249120423500' AND s.name = 'JDA';

-- Bonneville Dam: BON outflow (kcfs)
INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'usace.cda',
    'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22BON.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22%5D&timezone=GMT&backward=2d&forward=0d',
    '',
    1
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'BON', 'USACE', fu.id, NULL, NULL
FROM fetch_url fu
WHERE fu.url = 'https://www.nwd-wc.usace.army.mil/dd/common/web_service/webexec/getjson?query=%5B%22BON.Flow-Out.Ave.1Hour.1Hour.CBT-REV%22%5D&timezone=GMT&backward=2d&forward=0d'
  AND NOT EXISTS (
      SELECT 1 FROM source s WHERE s.name = 'BON' AND s.fetch_url_id = fu.id
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Bonneville_merge' AND s.name = 'BON';
