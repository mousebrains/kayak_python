-- Add display/sort columns to gauge.
--
-- These mirror the Mapped[] declarations added to src/kayak/db/models.py in
-- the same commit. scripts/seed_gauge_display.py populates them by running
-- the agency-metadata resolver + N→North normalization once; build.py then
-- reads directly from these columns instead of re-deriving names each build
-- from the (gitignored, potentially-missing) Gauge-metadata-cache/gauges.db.
--
-- sort_name encodes the full row order (basin → fork rank → elevation DESC
-- → DA ASC) as a single alphabetical key, so the build-time sort is plain
-- ORDER BY sort_name.

ALTER TABLE gauge ADD COLUMN river         TEXT;
ALTER TABLE gauge ADD COLUMN display_name  TEXT;
ALTER TABLE gauge ADD COLUMN sort_name     TEXT;
