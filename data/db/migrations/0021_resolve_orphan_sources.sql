-- Migration 0021: relink 5 orphan fetch-active sources to their gauges.
--
-- Phase 0 of docs/PLAN_orphan_sources.md. Companion to migration 0020
-- which fixed the calc-input orphans (sources 299, 300); this one
-- handles the 5 remaining orphan rows on prod, all wa.gov STG/WTM or
-- NWPS endpoints whose previous source-row siblings were deleted by
-- migration 0018 and were silently recreated by the parser's
-- _auto_create_source path without gauge_source links.
--
-- Mapping orphan -> gauge:
--   294 (29C100 wa.gov STG)  -> gauge 150 (29C100)
--   295 (29C100 wa.gov WTM)  -> gauge 150 (29C100)
--   296 (28B080 wa.gov STG)  -> gauge 184 (28B080)
--   297 (28B080 wa.gov WTM)  -> gauge 184 (28B080)
--   298 (WASW1 NWPS)         -> gauge 184 (28B080)
--
-- The DSG endpoints feeding the linked sibling sources (182 + 220)
-- emit only Discharge — verified via curl. The `gauge` and
-- `temperature` observations on those sibling rows (min observed_at
-- 2025-10-01, max 2026-05-11) were merged in by 0018's
-- INSERT OR IGNORE from now-deleted upstreams, then froze when 0018
-- removed those upstreams. Linking these 5 orphans is the only path
-- forward for gauge-height and water-temperature on these two
-- gauges; without this migration those two data_types stay pegged
-- to 2026-05-11 forever.
--
-- Source 298 is a secondary `gauge` feed for gauge 184 (sibling to
-- 296). Both emit gauge at ~15-min cadence from independent sensors;
-- update_latest_gauge picks the row with the later observed_at (and
-- higher source_id as the deterministic tie-break per cache.py:252).
-- The cached gauge value can flicker by a few hundredths of a foot
-- as which feed updates last alternates. Cosmetic.
--
-- INSERT OR IGNORE in case the rows have been added manually before
-- the migration lands on a given DB.

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (150, 294);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (150, 295);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 296);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 297);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (184, 298);
