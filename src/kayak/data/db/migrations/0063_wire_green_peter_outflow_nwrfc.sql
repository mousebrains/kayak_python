-- Migration 0063: wire a live NWRFC source for Green Peter Dam outflow.
--
-- The USACE source GPR (parser usace.cda, legacy nwd-wc.usace.army.mil
-- endpoint) froze ~2026-05-23 in the USACE legacy webexec freeze. GPR was the
-- ONLY source on Green_Peter_merge (gauge 107), so the gauge went stale. NWRFC
-- publishes the same Green Peter Dam outflow live as LID GPRO3 (pe=QR, river
-- discharge), read by the existing nwrfc.textplot parser (same as the live
-- FALO3 source): the parser maps the Discharge column to DataType.flow and
-- converts the page's PDT/PST timestamps itself, so no source.timezone is
-- needed (mirrors FALO3, timezone NULL). Adding GPRO3 as a SECOND source on
-- gauge 107 restores live outflow and lets the gauge ride through the USACE
-- freeze -- exactly how Fall_Creek_Lowell stays fresh via its USGS source.
-- (Only the flow/outflow side is restored here; the dead GPR also carried
-- inflow, which is secondary for paddling.)
--
-- Idempotent (re-runnable): fetch_url.url is UNIQUE -> INSERT OR IGNORE;
-- source.name is intentionally non-unique (models.py::Source) -> guard via
-- NOT EXISTS; gauge_source has a composite PK -> INSERT OR IGNORE. Rows are
-- linked by name/URL, so autoincrement ids are fine. The only colon is the
-- https:// scheme (not a `:word` bind marker), so no @no_transaction needed.

INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'nwrfc.textplot',
    'https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=GPRO3&pe=QR',
    '',
    1
);

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'GPRO3', 'NWRFC', fu.id, NULL, NULL
FROM fetch_url fu
WHERE fu.url = 'https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=GPRO3&pe=QR'
  AND NOT EXISTS (
      SELECT 1 FROM source s
      WHERE s.name = 'GPRO3' AND s.fetch_url_id = fu.id
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Green_Peter_merge' AND s.name = 'GPRO3';
