-- Migration 0015: move 11 Idaho Salmon / Selway-Lochsa / Klickitat NWRFC XML
-- gauges to the NWPS JSON API.
--
-- Why: the legacy nwrfc.noaa.gov xml.cgi endpoint is burst-rate-limited;
-- 2026-05-09T00:17Z pipeline timeout was triggered by 8 simultaneous NWRFC
-- 429s plus an unreachable USBR host. Moving stations that NWPS serves
-- shrinks the NWRFC URL count from 16 to 5, which stays under the burst
-- ceiling regardless of per-host concurrency.
--
-- Stations kept on NWRFC (NWPS returns 404 or only reservoir elevation):
--   CMRO3, LOCO3, WNFO3 (XML) and APLO3, FALO3 (textPlot).
--
-- Source rows for the 11 migrated stations are repointed at the new NWPS
-- fetch_url so observations continue to land on the same source_id (no
-- duplicate Source rows, no broken gauge_source links). The old NWRFC
-- fetch_url rows are flipped to is_active=0 — sync_sources would do the
-- same on next fetch, but doing it here keeps the migration self-contained.

INSERT OR IGNORE INTO fetch_url (url, parser, hours, is_active) VALUES
  ('https://api.water.noaa.gov/nwps/v1/gauges/SRYI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/MIDI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/KRSI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/JOHI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/RIGI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/WHBI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/SELI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/LOCI1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/STII1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/PITW1/stageflow/observed', 'nwps', '', 1),
  ('https://api.water.noaa.gov/nwps/v1/gauges/KLCW1/stageflow/observed', 'nwps', '', 1);

UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/SRYI1/stageflow/observed') WHERE name = 'SRYI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=SRYI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/MIDI1/stageflow/observed') WHERE name = 'MIDI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=MIDI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/KRSI1/stageflow/observed') WHERE name = 'KRSI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=KRSI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/JOHI1/stageflow/observed') WHERE name = 'JOHI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=JOHI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/RIGI1/stageflow/observed') WHERE name = 'RIGI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=RIGI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/WHBI1/stageflow/observed') WHERE name = 'WHBI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=WHBI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/SELI1/stageflow/observed') WHERE name = 'SELI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=SELI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/LOCI1/stageflow/observed') WHERE name = 'LOCI1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=LOCI1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/STII1/stageflow/observed') WHERE name = 'STII1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=STII1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/PITW1/stageflow/observed') WHERE name = 'PITW1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=PITW1&pe=HG&dtype=b&numdays=1');
UPDATE source SET fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/KLCW1/stageflow/observed') WHERE name = 'KLCW1' AND fetch_url_id = (SELECT id FROM fetch_url WHERE url = 'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=KLCW1&pe=HG&dtype=b&numdays=1');

UPDATE fetch_url SET is_active = 0 WHERE url IN (
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=SRYI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=MIDI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=KRSI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=JOHI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=RIGI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=WHBI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=SELI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=LOCI1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=STII1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=PITW1&pe=HG&dtype=b&numdays=1',
  'https://www.nwrfc.noaa.gov/xml/xml.cgi?id=KLCW1&pe=HG&dtype=b&numdays=1'
);
