-- Migration 0006: drop the unused `pages` cache table.
--
-- 0 rows on prod (audited 2026-04) and no readers in either Python or
-- PHP. docs/database-schema.md had already labelled it "currently unused".
-- The Page ORM model and the src/kayak/db/pages.py / page_db.py facade
-- were removed alongside this migration.
DROP TABLE IF EXISTS pages;
