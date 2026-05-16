-- Migration 0025: wipe merge-pollution rows from active sources.
--
-- Before `levels merge` was retired (c5bac0f), each manual run wrote the
-- median across every gauge's sources back into the lowest-source_id of
-- that gauge — for any data_type a sibling produced. The result was
-- agency-tagged source rows carrying observations of data types their
-- own parser never fetches:
--
--   - nwps parser publishes Stage + Flow. Any `temperature` rows on an
--     NWS-tagged source can only have come from a USGS sibling via the
--     manual merge step.
--   - usace.cda publishes Flow-In / Flow-Out (inflow + flow). Any
--     `gauge` or `temperature` rows on a usace.cda source came from
--     the merge too.
--
-- The fetch-usgs-ogc fallback can write into a single-source NWS row
-- when the gauge has a usgs_id and no sibling — that's how BRTO3 (4)
-- and NMFO3 (177) still get current temperature observations, so they
-- are deliberately excluded below. Every (source_id, data_type) listed
-- here has zero observations in the last 7 days, confirming nothing
-- else writes there. ~130k rows total.
--
-- The website reads gauge-level latest_gauge_observation, which already
-- picks the fresher USGS-OGC sibling on every affected gauge — no cache
-- row currently references one of these (source, data_type) pairs as
-- its source, so latest_gauge_observation needs no touch-up here.
--
-- After this runs the per-source pages (source.php / data.php) stop
-- listing dead data types for these sources, and status.php's per-
-- agency freshness buckets reflect what each parser actually fetches.

-- 39 NWS sources × temperature: merge wrote these from USGS siblings.
DELETE FROM observation
WHERE data_type = 'temperature'
  AND source_id IN (
        1,   2,   5,  13,  16,  20,  34,  35,  55,  57,
       58,  62,  63,  64,  66,  67,  69,  70,  83,  84,
       86,  87,  97,  98, 113, 120, 121, 125, 129, 131,
      141, 147, 148, 150, 151, 152, 175, 193, 217
  );

-- FAL (USACE, usace.cda): clear merge-fed gauge + temperature.
DELETE FROM observation
WHERE source_id = 171
  AND data_type IN ('gauge', 'temperature');

-- Stale per-source latest_observation cache entries for the same pairs.
-- update_latest would prune these the next time it runs for the (source,
-- data_type), but pruning here keeps the cache honest in the interim.
DELETE FROM latest_observation
WHERE data_type = 'temperature'
  AND source_id IN (
        1,   2,   5,  13,  16,  20,  34,  35,  55,  57,
       58,  62,  63,  64,  66,  67,  69,  70,  83,  84,
       86,  87,  97,  98, 113, 120, 121, 125, 129, 131,
      141, 147, 148, 150, 151, 152, 175, 193, 217
  );

DELETE FROM latest_observation
WHERE source_id = 171
  AND data_type IN ('gauge', 'temperature');
