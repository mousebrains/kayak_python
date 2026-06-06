-- Migration 0013: index FK columns that aren't already covered
--
-- Without these, "given a source/reach/guidebook, find related rows"
-- queries fall back to a full table scan. Tables are small today
-- (gauge_source 224, reach_class 401, reach_guidebook 1093,
-- latest_gauge_observation 461) so latency is fine, but adding the
-- indexes now removes the future scaling cliff and matches the
-- pattern already used for ix_reach_state_state_id.
--
-- gauge_source / reach_guidebook PKs are (left_id, right_id) composite.
-- The PK index serves "given left, find right" but not the reverse.
-- These supplemental indexes cover the reverse direction.

CREATE INDEX IF NOT EXISTS ix_gauge_source_source_id
    ON gauge_source (source_id);

CREATE INDEX IF NOT EXISTS ix_reach_class_reach_id
    ON reach_class (reach_id);

CREATE INDEX IF NOT EXISTS ix_reach_guidebook_guidebook_id
    ON reach_guidebook (guidebook_id);

CREATE INDEX IF NOT EXISTS ix_latest_gauge_observation_source_id
    ON latest_gauge_observation (source_id);
