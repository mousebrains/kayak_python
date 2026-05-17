-- Migration 0028: add provenance_slug to calc_expression so calc gauges
-- whose formula was derived from a regression fit can point at their
-- writeup under docs/regression/<slug>.md (and the .svg / .json siblings
-- the build copies into /static/regression/).
--
-- NULL means the calc is operational (a ratio, sum, etc.) rather than
-- a regression fit and has no analysis doc.

ALTER TABLE calc_expression ADD COLUMN provenance_slug TEXT;

-- Backfill the Rogue_Above_Prospect_calc row introduced in migration 0027.
-- Matches on the canonical reference handle in time_expression so the
-- backfill is idempotent regardless of the calc_expression row id.
UPDATE calc_expression
SET provenance_slug = 'rogue_14328000_from_14330000'
WHERE time_expression = 'rp::14330000::flow'
  AND provenance_slug IS NULL;
