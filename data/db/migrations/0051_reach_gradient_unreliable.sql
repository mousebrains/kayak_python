-- Migration 0051: add reach.gradient_unreliable boolean + suppress 6 reaches
--
-- Six reaches show extreme-peak gradient_profile warnings (samples >
-- 1500 ft/mi) that can't be resolved by endpoint moves:
--
--   * Sheep Creek (155), Jarbidge (127), SF Coquille (186) — 100%
--     LIDAR-sourced canyon/gorge reaches whose network-traced channel
--     intersects canyon walls / cliff faces; LIDAR captures the walls
--     accurately but the data does not describe water gradient.
--   * EF Owyhee (134, 314) — 0% LIDAR coverage in remote SE Oregon;
--     1/3 arc-second 3DEP samples include canyon-wall artifacts that
--     can't be distinguished from real channel features at 10 m cells.
--   * Butte Creek (262) — Butte Creek Falls sits between the post-0049
--     put-in and the operator-set take-out; computed gradient is
--     dominated by a non-paddleable feature.
--
-- For these reaches, the trace-derived gradient is unreliable —
-- suppress the calculation so the chart doesn't render and the
-- max_gradient column doesn't carry misleading numbers. A future
-- channel-snapped retrace could re-enable computation; for now the
-- flag lets compute_reach_gradient.py skip them cleanly.
--
-- gradient_unreliable defaults FALSE so existing rows keep computing.
-- Phase 2B's compute_reach_gradient.py reads this column and skips;
-- emit_max_gradient_migration.py's WHERE filter naturally excludes
-- the now-NULL values.
--
-- The matching SQLAlchemy column declaration lands in
-- src/kayak/db/models.py in the same commit — without it,
-- `levels init-db` on a fresh DB would create a Reach table that
-- lacks this column (init-db rebuilds via Base.metadata.create_all()).

ALTER TABLE reach ADD COLUMN gradient_unreliable BOOLEAN DEFAULT 0 NOT NULL;

-- Set the flag + NULL out the now-invalid computed values
UPDATE reach
SET gradient_unreliable = 1,
    max_gradient = NULL,
    gradient_profile = NULL
WHERE id IN (
    127,   -- Jarbidge (aw_561)
    134,   -- EF Owyhee (aw_580)
    155,   -- Sheep Creek (aw_625)
    186,   -- SF Coquille (aw_11663)
    262,   -- Butte Creek (aw_10940)
    314    -- EF Owyhee (aw_581)
);
