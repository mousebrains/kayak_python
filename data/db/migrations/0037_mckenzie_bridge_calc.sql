-- Migration 0037: revive USGS 14159000 (McKenzie at McKenzie Bridge,
-- retired 1994-09-29) as a calc gauge. Wraps a 5-predictor OLS fit in
-- a direct upper-bound ceiling at McKenzie_Rainbow (gauge 177, USGS
-- 14159110 via NWRFC CMRO3) and a Trail Bridge floor.
--
-- Underlying OLS fit (docs/regression/mckenzie_14159000_from_vida_trailbridge_sfrainbow_sfcougar_lookout.md):
--   14159000_est = 0.0691955 * Vida (14162500)
--                + 1.21419   * Trail Br Dam (14158850)
--                - 0.0930529 * SF Rainbow (14159500)
--                + 0.116552  * SF Cougar (14159200)
--                + 0.561507  * Lookout (14161500)
--                + 162.1
--   r2 = 0.9847, RMSE = 94.9 cfs, n = 6938 daily means,
--   window 1968-10-01..1994-09-29.
--
-- Trail Bridge dominates (coef ~1.21) because McKenzie Bridge sits
-- ~7 mi downstream of the Trail Bridge Dam outlet on the mainstem.
-- Fit is stable across sub-windows (r2 0.977-0.985 over 1963-1990).
--
-- Envelope:
--   least(
--     McKenzie_Rainbow,                    -- physical upper bound (downstream gauge)
--     greatest(
--       Trail Bridge,                       -- upstream-input floor
--       <OLS regression>                    -- best estimate
--     )
--   )
--
-- McKenzie_Rainbow (kayak gauge 177, NWRFC LID CMRO3, USGS 14159110)
-- sits ~3 mi downstream of McKenzie Bridge and just upstream of the
-- South Fork McKenzie confluence. Its drainage (526 sq mi) strictly
-- contains the target's drainage (348 sq mi) plus only the intervening
-- 178 sq mi (Boulder Cr, Lost Cr, Anderson Cr, Horse Cr). So
-- McKenzie_Rainbow::flow is a tight, exact physical upper bound on
-- McKenzie at McKenzie Bridge -- no mass-balance approximation. NWRFC
-- emits Discharge alongside Stage in its observed XML (computed via
-- their local rating curve) -- USGS site 14159110 stopped publishing
-- 00060 in 2006, but the NWRFC feed is the operational successor
-- consumed by kayak's nwrfc.xml parser, src/kayak/parsers/nwrfc_xml.py.
--
-- The legacy WKCC formula used Vida - SF_Rainbow - Blue as its mass-
-- balance ceiling, an approximation made when 14159110 was off-line.
-- Now that NWRFC publishes computed flow for the site, the
-- approximation can be replaced with the direct downstream measurement
-- and Blue River drops out of the formula entirely.
--
-- The Trail Bridge floor prevents the regression from going below the
-- dominant upstream input under predictor combinations the fit never
-- saw. Outer greatest(0, ..) guards against negative values from
-- extreme tributary spikes.
--
-- At current real-time flows (Vida 2490, Trail Br 748, SF Rainbow 350,
-- SF Cougar 301, Lookout 29.1, McKenzie_Rainbow 2070): regression
-- ~1261, floor 748, ceiling 2070 -> calc emits 1261. Legacy formula
-- emits ~1220 (+3.4% delta, well within the 1-sigma scatter of 95 cfs).

------------------------------------------------------------------------------
-- PART A: calc_expression
--
-- All 6 referenced gauges (vd, tb, sr, sc, lk, mr) are pre-existing in
-- the kayak DB; this migration creates no new predictor gauges. The
-- mr handle points at gauge id 177 (McKenzie_Rainbow) which ingests
-- NWRFC CMRO3 via fetch_url 83 (nwrfc.xml, pe=HG). No calc-on-calc
-- dependencies: all 6 predictors are direct (USGS or NWRFC) feeds.
------------------------------------------------------------------------------

INSERT INTO calc_expression (data_type, expression, time_expression, note, provenance_slug)
SELECT
    'flow',
    'round(greatest(0, least(mr::McKenzie_Rainbow::flow, greatest(tb::14158850::flow, 0.0691955 * vd::MCKENZIE_VIDA_merge::flow + 1.21419 * tb::14158850::flow - 0.0930529 * sr::SF_McKenzie_near_Rainbow::flow + 0.116552 * sc::SF_McKenzie_Cougar_merge::flow + 0.561507 * lk::Lookout_Blue_merge::flow + 162.1))))',
    'vd::MCKENZIE_VIDA_merge::flow tb::14158850::flow sr::SF_McKenzie_near_Rainbow::flow sc::SF_McKenzie_Cougar_merge::flow lk::Lookout_Blue_merge::flow mr::McKenzie_Rainbow::flow',
    'McKenzie at McKenzie Bridge (USGS 14159000 retired 1994-09-29) estimated from 5-predictor OLS fit (Vida, Trail Br Dam, SF nr Rainbow, SF abv Cougar, Lookout Cr) wrapped in a McKenzie_Rainbow ceiling and Trail Bridge floor. n=6938 daily means / 19 yr overlap 1968-10-01..1994-09-29, r2=0.9847, RMSE=94.9 cfs. Trail Bridge dominates (coef 1.21) since McKenzie Bridge sits just downstream. Ceiling is the next downstream gauge (NWRFC CMRO3, kayak gauge 177): a strict physical upper bound, replacing the legacy Vida minus SF_Rainbow minus Blue mass-balance approximation. Legacy WKCC formula agrees with this fit to within ~3.4 percent at typical flows. See docs/regression/mckenzie_14159000_from_vida_trailbridge_sfrainbow_sfcougar_lookout.md.',
    'mckenzie_14159000_from_vida_trailbridge_sfrainbow_sfcougar_lookout'
WHERE NOT EXISTS (
    SELECT 1 FROM calc_expression
    WHERE provenance_slug = 'mckenzie_14159000_from_vida_trailbridge_sfrainbow_sfcougar_lookout'
);

------------------------------------------------------------------------------
-- PART B: new calc gauge at the historical USGS 14159000 location.
--
--   USGS 14159000 "MCKENZIE R AT MCKENZIE BRIDGE, OREG.":
--     lat 44.179012, lon -122.130335
--     elevation 1419.04 ft (NGVD29), drainage area 348 sq mi
--     HUC12 170900040209 "Florence Creek-McKenzie River"
--
-- sort_name encodes basin|fork|10000-elev|drainage_area per the
-- existing McKenzie row convention: 10000 - 1419 = 008581.
------------------------------------------------------------------------------

INSERT OR IGNORE INTO gauge (
    name, location, latitude, longitude, river,
    display_name, sort_name, elevation, drainage_area, huc,
    allow_negative_flow, state
) VALUES (
    'McKenzie_McKenzie_Bridge_calc',
    'Near McKenzie Bridge (estimated)',
    44.179012, -122.130335,
    'McKenzie', 'McKenzie at McKenzie Bridge (calc)',
    'mckenzie|9|008581|000348',
    1419.04, 348.0, '170900040209',
    0, 'OR'
);

------------------------------------------------------------------------------
-- PART C: source pointing at the new calc_expression, plus gauge_source link.
------------------------------------------------------------------------------

INSERT INTO source (name, agency, fetch_url_id, calc_expression_id, timezone)
SELECT 'McKenzie_McKenzie_Bridge_calc', 'Calculation', NULL, ce.id, ''
FROM calc_expression ce
WHERE ce.provenance_slug = 'mckenzie_14159000_from_vida_trailbridge_sfrainbow_sfcougar_lookout'
  AND NOT EXISTS (
      SELECT 1 FROM source s WHERE s.name = 'McKenzie_McKenzie_Bridge_calc'
  );

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id)
SELECT g.id, s.id FROM gauge g, source s
WHERE g.name = 'McKenzie_McKenzie_Bridge_calc'
  AND s.name = 'McKenzie_McKenzie_Bridge_calc';
