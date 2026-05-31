-- Migration 0072: rename the wa.gov (WA DOE) sources from the bare station id
-- to the per-file filename stem, so source.name is unique within the table.
--
-- WA DOE publishes one *_FM.TXT file per (station, parameter): STG (stage),
-- DSG (discharge), WTM (water temp). The wa.gov parser reads the bare station
-- id (29C100) from each file's header, so all three files mapped to sources
-- named `29C100` — three rows sharing a name (likewise `28B080`). That blocks
-- the `UNIQUE(source.name)` the metadata-single-source redesign needs (name is
-- the symbolic foreign key). Rename each source to its file's stem.
--
-- Matching stays correct after the rename: every wa.gov fetch_url has exactly
-- one source, so fetch.py sets `source_id` directly and dump_to_db keys on it,
-- not on the (now-differing) name. Timezone localization is preserved by the
-- single-tz fallback added to BaseParser._localize in the same change (the
-- parser still emits `29C100`, but the lone Etc/GMT+8 on the fetch is applied).
--
-- Renamed BY the source's fetch_url (the 3 same-named rows differ only by URL).
-- Idempotent: re-running matches nothing once renamed (the `name IN (...)` guard
-- no longer holds). No colon bind markers → no @no_transaction.

UPDATE source SET name = '29C100_STG_FM'
WHERE name = '29C100'
  AND fetch_url_id = (SELECT id FROM fetch_url
                      WHERE url LIKE '%/29C100/29C100_STG_FM.TXT');
UPDATE source SET name = '29C100_DSG_FM'
WHERE name = '29C100'
  AND fetch_url_id = (SELECT id FROM fetch_url
                      WHERE url LIKE '%/29C100/29C100_DSG_FM.TXT');
UPDATE source SET name = '29C100_WTM_FM'
WHERE name = '29C100'
  AND fetch_url_id = (SELECT id FROM fetch_url
                      WHERE url LIKE '%/29C100/29C100_WTM_FM.TXT');

UPDATE source SET name = '28B080_STG_FM'
WHERE name = '28B080'
  AND fetch_url_id = (SELECT id FROM fetch_url
                      WHERE url LIKE '%/28B080/28B080_STG_FM.TXT');
UPDATE source SET name = '28B080_DSG_FM'
WHERE name = '28B080'
  AND fetch_url_id = (SELECT id FROM fetch_url
                      WHERE url LIKE '%/28B080/28B080_DSG_FM.TXT');
UPDATE source SET name = '28B080_WTM_FM'
WHERE name = '28B080'
  AND fetch_url_id = (SELECT id FROM fetch_url
                      WHERE url LIKE '%/28B080/28B080_WTM_FM.TXT');
