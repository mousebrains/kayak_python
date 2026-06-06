-- Migration 0017: drop the now-unused NWRFC XML fetch_url rows that
-- migration 0015 left flagged is_active=0, and tidy up the source.agency
-- column.
--
-- 1. Migration 0015 repointed 11 Idaho / Selway-Lochsa / Klickitat sources
--    from the NWRFC XML endpoint to NWPS but kept the old fetch_url rows
--    around with is_active=0. Nothing references them anymore (sync_sources
--    only touches active rows) so they can go.
--
-- 2. A handful of sources accumulated blank or parser-named agency strings.
--    Fill agency='USGS' on the rows whose name is a USGS station number,
--    name IRGC1 as NOAA (NWPS-served), and rewrite the parser-name leakage
--    (usace.cda, nwrfc.xml, nwrfc.textplot) into proper agency names.

DELETE FROM fetch_url
WHERE parser = 'nwrfc.xml' AND is_active = 0
  AND id NOT IN (SELECT fetch_url_id FROM source WHERE fetch_url_id IS NOT NULL);

UPDATE source SET agency = 'USGS'
WHERE (agency IS NULL OR agency = '')
  AND name IN (
    '14201500','14091500','14188800','14316500','14178000',
    '14305500','14306500','14145500','14020000'
  );

UPDATE source SET agency = 'NOAA'
WHERE name = 'IRGC1' AND (agency IS NULL OR agency = '');

UPDATE source SET agency = 'USACE' WHERE agency = 'usace.cda';
UPDATE source SET agency = 'NWRFC' WHERE agency IN ('nwrfc.xml','nwrfc.textplot');
