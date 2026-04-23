-- Migration 0005: align live index names with the ORM-declared names.
--
-- Before the table was renamed from `section` to `reach` the indexes were
-- named `ix_section_sort_name` and `ix_section_state_state_id`. The model
-- declares `ix_reach_sort_name` and `ix_reach_state_state_id`, so fresh
-- DBs get the new names from `Base.metadata.create_all()`. This migration
-- closes the gap on pre-rename live DBs — SQLite can't ALTER INDEX
-- RENAME, so DROP + CREATE is the only path.
DROP INDEX IF EXISTS ix_section_sort_name;
DROP INDEX IF EXISTS ix_section_state_state_id;
CREATE INDEX IF NOT EXISTS ix_reach_sort_name      ON reach (sort_name);
CREATE INDEX IF NOT EXISTS ix_reach_state_state_id ON reach_state (state_id);
