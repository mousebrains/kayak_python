-- Migration 0041: strip the LINESTRING(...) wrapper from Horse Creek's geom.
--
-- Migration 0039 stored the reach geometry as a WKT string
-- "LINESTRING(-122.021835 44.104762, ..., -122.175855 44.173093)".
-- The kayak convention (and the PHP map parser at
-- php/includes/gauge_map.php:61-70) expects raw "lon lat,lon lat" pairs
-- with no wrapper. PHP's parser:
--
--   foreach (explode(',', $s) as $pair) {
--       $parts = preg_split('/\s+/', trim($pair));
--       ...
--       $out[] = [(float)$parts[1], (float)$parts[0]];  // [lat, lon]
--   }
--
-- splits on commas, then float-casts each token. The first token from
-- our wrapped value is "LINESTRING(-122.021835". PHP's (float) cast
-- stops at the first non-numeric character — there's no leading
-- numeric, so the result is 0. The first polyline vertex lands at
-- (44.104762°N, 0°E) — Bay of Biscay off France — and the rendered
-- polyline draws a line from there to the next valid Oregon vertex,
-- producing the long horizontal line operators reported on
-- gauge.php?id=211.
--
-- Fix: drop the "LINESTRING(" prefix (11 chars) and the trailing ")"
-- (1 char). The trailing ")" was previously harmless to the parser
-- because PHP's (float) cast happily stops at non-numerics in token
-- suffixes — but stripping it costs nothing and matches the established
-- on-disk format used by every other reach.
--
-- Spaces after commas (we emitted "lon lat, lon lat") are fine — the
-- PHP parser's trim($pair) handles whitespace either way. Most existing
-- reaches store "lon lat,lon lat" (no space) but the parser is
-- tolerant.
--
-- Idempotent via the LIKE guard.

UPDATE reach
SET geom = substr(geom, 12, length(geom) - 12)
WHERE aw_id = 2868
  AND geom LIKE 'LINESTRING(%';
