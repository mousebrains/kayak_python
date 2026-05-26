-- Migration 0060: clear gradient_unreliable on reaches 134 (EF Owyhee) and
-- 186 (SF Coquille) so the sequential-migrate path matches reach.csv / prod.
--
-- 0051 set gradient_unreliable=1 for these two; 0058's lift-list (which set it
-- back to 0 for the reaches the post-snap algorithm now handles) omitted them,
-- yet 0059 wrote their max_gradient + gradient_profile. So a fresh
-- `levels migrate` from 0001 left 134/186 flagged-unreliable but carrying
-- gradient data -- the chart suppresses data it was just given, and the state
-- diverges from reach.csv (which has both at 0). This aligns them. Idempotent.
--
-- (No BEGIN/COMMIT: the migration runner wraps each file in a transaction.)

UPDATE reach SET gradient_unreliable = 0 WHERE id IN (134, 186);
