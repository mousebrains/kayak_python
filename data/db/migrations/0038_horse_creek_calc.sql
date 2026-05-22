-- Migration 0038: revive Horse Creek (USGS 14159100, intermittent;
-- last flow 2026-04-02 with a 1969-2023 gap in the record) as a calc
-- gauge. Two-predictor OLS fit dominated by SF Cougar (with Trail
-- Bridge as a regulated-mainstem secondary), wrapped in a physical
-- ceiling at the McKenzie_Rainbow - McKenzie_Bridge_calc difference.
--
-- Underlying OLS fit (docs/regression/horse_14159100_from_sfcougar_trailbridge.md):
--   14159100_est = 0.403663 * SF_Cougar (14159200)
--                + 0.217772 * Trail Br Dam (14158850)
--                + 53.43
--   r2 = 0.9354, RMSE = 88.4 cfs, n = 3508 daily means,
--   window 1962-10-01..2026-04-01 (full Horse Creek POR overlap; the
--   record is non-contiguous, 1962-1969 + 2023-2026).
--
-- SF Cougar is the dominant predictor (coef ~0.40, exactly matching
-- the topography: Horse Creek and the upper SF McKenzie drain
-- adjacent south-side high-Cascade terrain with similar
-- elevation/aspect/snowpack regimes). Trail Bridge adds the regulated
-- mainstem signal (coef ~0.22).
--
-- Predictor selection: a 5-predictor fit using the same set as
-- McKenzie_Bridge_calc (Vida, Trail Br, SF Rainbow, SF Cougar,
-- Lookout) gave r2 = 0.9345, RMSE = 91.9 on n = 3173 (335 fewer
-- pairings because of predictor gaps). The 2-predictor fit is both
-- more accurate AND uses more data. Lookout was tested as a
-- predictor and added nothing (coef essentially zero in the 5-pred
-- fit). Vida as a second predictor was tested and rejected
-- (r2 = 0.9224, RMSE = 96.9 on n = 3508): Trail Bridge captures the
-- regulated mainstem better than Vida at this latitude.
--
-- Envelope:
--   least(
--     McKenzie_Rainbow - McKenzie_Bridge_calc,   -- physical ceiling
--     <OLS regression>                              -- best estimate
--   )
-- floored at 0 via the outer greatest(0, ..).
--
-- The ceiling encodes the mass-balance constraint that Horse Creek
-- (joining the McKenzie between gauges 14159000 and 14159110) cannot
-- exceed the difference between those gauges, since the difference
-- includes Horse Creek plus Lost/Boulder/Anderson/minor tribs.
-- McKenzie_Rainbow is gauge 177 (NWRFC CMRO3, USGS 14159110, computed
-- discharge from the local rating curve); McKenzie_Bridge_calc is the
-- calc gauge introduced in migration 0037. Horse Creek drainage
-- (149.6 sq mi) is about 84 percent of the 178 sq mi intervening
-- drainage, so the regression typically lands well below this
-- ceiling; the ceiling binds only at edge cases where the two
-- bracketing gauges disagree.
--
-- No floor beyond 0: Horse Creek can absolutely sit below Trail
-- Bridge's flow (much smaller drainage, summer baseflow), so a
-- Trail-Bridge floor in the Bridge-calc pattern would be wrong here.
--
-- At current real-time flows (SF Cougar 301, Trail Br 748,
-- McKenzie_Rainbow 2070, McKenzie_Bridge_calc 1261): regression
-- ~338 cfs, ceiling 809, output 338 cfs.

------------------------------------------------------------------------------
-- PART A: calc_expression
--
-- 4 referenced gauges: sc (SF_McKenzie_Cougar_merge), tb (14158850),
-- mr (McKenzie_Rainbow), mb (McKenzie_McKenzie_Bridge_calc). The
-- mb handle points at the calc gauge created in migration 0037;
-- calculator._topo_sort_calc_sources will run McKenzie_Bridge_calc
-- before Horse_Creek_calc each pipeline tick.
------------------------------------------------------------------------------

INSERT INTO calc_expression (data_type, expression, time_expression, note, provenance_slug)
SELECT
    'flow',
    'round(greatest(0, least(mr::McKenzie_Rainbow::flow - mb::McKenzie_McKenzie_Bridge_calc::flow, 0.403663 * sc::SF_McKenzie_Cougar_merge::flow + 0.217772 * tb::14158850::flow + 53.43)))',
    'sc::SF_McKenzie_Cougar_merge::flow tb::14158850::flow mr::McKenzie_Rainbow::flow mb::McKenzie_McKenzie_Bridge_calc::flow',
    'Horse Creek near McKenzie Bridge (USGS 14159100, last flow 2026-04-02 with a 1969-2023 gap in the record) estimated from a 2-predictor OLS fit: 0.404 * SF_Cougar + 0.218 * Trail_Bridge + 53.4. n=3508 daily means / full Horse Creek POR overlap 1962-10-01..2026-04-01, r2=0.9354, RMSE=88.4 cfs. SF Cougar dominates (coef 0.40) because Horse Creek and upper SF McKenzie drain adjacent south-side high-Cascade terrain. Ceiling enforces the mass-balance constraint that Horse Creek cannot exceed McKenzie_Rainbow minus McKenzie_Bridge_calc (the total intervening flow between the bracketing gauges). See docs/regression/horse_14159100_from_sfcougar_trailbridge.md.',
    'horse_14159100_from_sfcougar_trailbridge'
WHERE NOT EXISTS (
    SELECT 1 FROM calc_expression
    WHERE provenance_slug = 'horse_14159100_from_sfcougar_trailbridge'
);

------------------------------------------------------------------------------
-- PART B: new calc gauge at the historical USGS 14159100 location.
--
--   USGS 14159100 "HORSE CREEK NEAR MCKENZIE BRIDGE, OR":
--     lat 44.162694, lon -122.154000
--     drainage area 149.6 sq mi
--     HUC12 170900040105 "Lower Horse Creek"
--   USGS metadata has no published altitude; left NULL.
--
-- sort_name: river|fork|10000-elev|drainage_area. Elevation unknown
-- so the elevation token is 999999 (existing-row convention for
-- unknowns; see e.g. SF Rainbow row 176).
------------------------------------------------------------------------------

INSERT OR IGNORE INTO gauge (
    name, location, latitude, longitude, river,
    display_name, sort_name, drainage_area, huc,
    allow_negative_flow, state
) VALUES (
    'Horse_Creek_calc',
    'Near McKenzie Bridge (estimated)',
    44.162694, -122.154000,
    'Horse Creek', 'Horse Creek near McKenzie Bridge (calc)',
    'horse creek|9|999999|000150',
    149.6, '170900040105',
    0, 'OR'
);

------------------------------------------------------------------------------
-- PART C: source pointing at the new calc_expression, plus gauge_source link.
------------------------------------------------------------------------------

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'Horse_Creek_calc', 'Calculation', NULL, ce.id, ''
FROM calc_expression ce
WHERE ce.provenance_slug = 'horse_14159100_from_sfcougar_trailbridge'
  AND NOT EXISTS (
      SELECT 1 FROM source s WHERE s.name = 'Horse_Creek_calc'
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'Horse_Creek_calc'
  AND s.name = 'Horse_Creek_calc';
