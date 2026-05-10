-- Migration 0016: index unindexed FK columns
--
-- Closes the round-2 follow-ups to migration 0013. Without these,
-- "given X, find related Y" queries (e.g. /gauge.php's reach lookup)
-- fall back to a full scan of a supplemental index.
--
-- Most user-visible win is ix_reach_gauge_id: gauge.php's
--   SELECT ... FROM reach r WHERE r.gauge_id = ? ORDER BY r.sort_name
-- previously planned as SCAN reach USING INDEX ix_reach_sort_name.
-- With this index it becomes a SEARCH.
--
-- The remaining six are FK columns whose ON DELETE SET NULL (or CASCADE)
-- cascade scans on parent deletion. Cheap insurance.

CREATE INDEX IF NOT EXISTS ix_reach_gauge_id              ON reach(gauge_id);
CREATE INDEX IF NOT EXISTS ix_edit_history_cr_id          ON edit_history(change_request_id);
CREATE INDEX IF NOT EXISTS ix_editor_reviewed_by          ON editor(reviewed_by);
CREATE INDEX IF NOT EXISTS ix_change_request_reviewed_by  ON change_request(reviewed_by);
CREATE INDEX IF NOT EXISTS ix_source_fetch_url_id         ON source(fetch_url_id);
CREATE INDEX IF NOT EXISTS ix_source_calc_expression_id   ON source(calc_expression_id);
CREATE INDEX IF NOT EXISTS ix_gauge_rating_id             ON gauge(rating_id);
