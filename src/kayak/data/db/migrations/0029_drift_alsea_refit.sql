-- Migration 0029: refresh DRIFT_ALSEA_calc with a fitted estimator of
-- USGS 14306600 (Drift Cr near Salado, Alsea drainage), drainage-area-
-- scaled to the take-out location which is the calc gauge anchor.
-- Also annotate DRIFT_SILETZ_calc to record that its 0.25 multiplier is
-- a drainage-area-ratio guess (no historical Drift-Siletz gauge exists).
--
-- See docs/regression/drift_alsea_14306600_from_14306500.md for the fit.
--
-- Drainage areas from NHD HR (HUC 1710, NHDPlusFlowlineVAA.TotDASqKm):
--   USGS 14306600 (historical gauge): 53.32 km^2 = 20.59 sq mi
--   Take-out / DRIFT_ALSEA_calc loc:  176.73 km^2 = 68.23 sq mi
--   Ratio: 176.73 / 53.32 = 3.3145
-- USGS-published DA for 14306600 (20.50 sq mi) agrees with NHD to 0.4%.
--
-- The raw fit (Drift_Salado = 0.0748577 * Alsea + 8.266, r2=0.9152,
-- RMSE=51.2 cfs) is multiplied through by the drainage-area ratio to
-- estimate flow at the take-out instead of at the gauge. The legacy
-- 0.30 * Alsea coefficient was effectively this same scaling applied
-- to a guessed slope -- the new value is the same magnitude but
-- backed by a real fit.
--
-- Why match calc rows by joining through source.name: time_expression
-- ('q::14306500::flow') is shared by calcs 2, 4, 6 (DRIFT_ALSEA,
-- NF_ALSEA, SF_ALSEA all consume Alsea-near-Tidewater), so it is not a
-- unique key. The source row pointing at the calc *is* unique.

------------------------------------------------------------------------------
-- DRIFT_ALSEA_calc: replace the legacy 0.30 scalar with the fitted
-- single-predictor line scaled to take-out drainage area.
--
--   Drift_takeout = 3.3145 * (0.0748577 * Alsea + 8.266)
--                 = 0.248113 * Alsea + 27.40
--
-- Underlying fit (against USGS 14306600, before DA scaling):
--   n = 3802 daily means, window 1958-09-01..1970-09-29
--   r2 = 0.9152, RMSE = 51.2 cfs
------------------------------------------------------------------------------

UPDATE calc_expression
SET expression = 'round(greatest(0, 0.248113 * q::14306500::flow +27.4))',
    note = 'Drift Cr (Alsea) at take-out estimated from Alsea-near-Tidewater (USGS 14306500). Underlying OLS fit against USGS 14306600 (Drift near Salado, retired 1970-09-29): slope 0.0748577, intercept 8.266, r2=0.9152, RMSE=51.2 cfs, n=3802 daily means / ~10.4 yr overlap 1958-09-01..1970-09-29. Coefficients here are scaled by drainage-area ratio 3.3145 = 176.73 km^2 (take-out per NHD HR) / 53.32 km^2 (Salado gauge). Window-stable: pre-scale slope 0.0698-0.0749 across 2.1-10.4 yr sub-windows. Heteroscedastic at high flow (Q5 residual std 105 cfs at gauge, ~350 cfs at take-out). See docs/regression/drift_alsea_14306600_from_14306500.md.',
    provenance_slug = 'drift_alsea_14306600_from_14306500'
WHERE id = (SELECT calc_expression_id FROM source WHERE name = 'DRIFT_ALSEA_calc');

------------------------------------------------------------------------------
-- DRIFT_SILETZ_calc: leave the 0.25 multiplier in place; document that
-- it is a drainage-area-ratio estimate with no underlying regression.
-- USGS NWIS has no historical gauge on Drift Creek (Siletz drainage).
-- The closest USGS "Drift Creek" with discharge data is 14306600 in
-- the Alsea drainage (used above); 14200100 near Silverton is in the
-- Pudding/Molalla basin, wrong climate and 2014-2017 only.
------------------------------------------------------------------------------

UPDATE calc_expression
SET note = 'Drift Cr (Siletz) estimated as 0.25 * Siletz-at-Siletz (USGS 14305500). Drainage-area-ratio scalar, NOT a regression fit. No historical USGS gauge exists on Drift Creek in the Siletz drainage. Refit if a state or local gauge becomes available.'
WHERE id = (SELECT calc_expression_id FROM source WHERE name = 'DRIFT_SILETZ_calc');

------------------------------------------------------------------------------
-- Backfill drainage_area on the calc gauge row to match the take-out
-- value the regression coefficients above are scaled to. The calc
-- expression itself is unchanged: 0.248113 = 3.3145 * 0.0748577 and
-- 27.40 = 3.3145 * 8.266 already, so this is metadata only -- surfaces
-- on /gauge.php and /map.html but does not affect computed flow.
------------------------------------------------------------------------------

UPDATE gauge
SET drainage_area = 68.23
WHERE name = 'DRIFT_ALSEA_calc';
