-- Add a state column to gauge (two-letter abbreviation, e.g. "OR").
--
-- Until now the gauges.html filter bar derived state per gauge by walking the
-- reaches that link to it; gauges with no linked reaches fell into a stray
-- "(no HUC)" pill. Storing state directly on gauge lets _build_gauges_filter_bar
-- be self-contained and removes the orphan bucket.
--
-- Backfill is by scripts/backfill_gauge_state.py: tier 1 looks up
-- usgs_site.state_cd in Gauge-metadata-cache/gauges.db; tier 2 falls back to
-- the distinct state of any linked reach.

ALTER TABLE gauge ADD COLUMN state TEXT;
