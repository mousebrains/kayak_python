-- Migration 0040: link the Horse Creek reach (AW 2868, created by
-- migration 0039) into reach_state + reach_class.
--
-- Migration 0039 inserted the reach row but stopped short of the
-- two join tables the build uses for filtering / class display:
--
--   * reach_state — without a row here, the rendered <tr> carries
--     data-state="" and the state-filter pills can't match it, so
--     filtering by Oregon (or any state) hides the reach from the
--     list. Visible by operator report 2026-05-22.
--   * reach_class — without a row here, the rendered <tr> carries
--     data-tier="?", and the class-filter pills can't match it. The
--     class string is in reach.difficulties as "III" but the build
--     reads tier from reach_class.name (not from difficulties).
--
-- Both joins use lookups via the reach.aw_id rather than a hard-coded
-- reach.id 407 so the migration is robust if reach 407 ever gets
-- recreated under a different id.
--
-- reach_class.low / .high left NULL — AW reach 2868 has no AW-
-- assigned gauge, so no published low / optimal / high flow values
-- to import. The kayak maintainer editing flow can fill these in
-- through the website. (12 existing reach_class rows in the live DB
-- already have NULL low/high — this is the convention for reaches
-- without AW flow guidance.)
--
-- Idempotent: INSERT OR IGNORE handles re-runs.

INSERT OR IGNORE INTO reach_state (reach_id, state_id)
SELECT r.id, s.id
FROM reach r, state s
WHERE r.aw_id = 2868
  AND s.name = 'Oregon';

INSERT OR IGNORE INTO reach_class (reach_id, name, low, high, low_data_type, high_data_type)
SELECT r.id, 'III', NULL, NULL, NULL, NULL
FROM reach r
WHERE r.aw_id = 2868
  AND NOT EXISTS (
      SELECT 1 FROM reach_class rc WHERE rc.reach_id = r.id AND rc.name = 'III'
  );
