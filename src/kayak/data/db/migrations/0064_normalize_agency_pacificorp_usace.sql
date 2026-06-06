-- Fold two legacy/slug source.agency values into canonical names — a review-3
-- follow-up to 0062 (which normalized the other slugs).
--
-- pacificorp -> PacifiCorp: the raw parser slug fell through canonical_agency's
-- map. init_db.py now maps it, so `levels fetch` keeps source 312 canonical
-- instead of reverting it to the slug (pacificorp IS in sources.yaml).
--
-- "USGS USACE" -> USGS: a 30+-year-old label from when USACE was the only feed
-- for these 3 gauges (McKenzie/Vida, Nehalem/Foss, Wilson/Tillamook). They are
-- USGS-OGC sources fetched by usgs_id with NO live USACE feed — the genuine
-- USACE sources are already tagged "USACE" — so they fold into USGS like the
-- other 171 OGC sources. They are NOT in sources.yaml, so fetch/sync_sources
-- never re-derive their agency; the value sticks.
--
-- Idempotent: once normalized, no row matches the predicates again.
UPDATE source SET agency = 'PacifiCorp' WHERE agency = 'pacificorp';
UPDATE source SET agency = 'USGS'       WHERE agency = 'USGS USACE';
