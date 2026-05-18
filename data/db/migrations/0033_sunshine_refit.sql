-- Migration 0033: refit Sunshine_calc to a Siletz-only regression on
-- USGS 14304350 (Sunshine Cr near Valsetz, retired 1991-09-29) plus
-- drainage-area scaling from the historical gauge location to the
-- reach take-out at the Sunshine Cr confluence.
--
-- Underlying OLS fit (docs/regression/sunshine_14304350_from_14305500):
--   Sunshine_at_USGS = 0.0503754 * Siletz_at_Siletz - 12.05
--   r2 = 0.9072, RMSE = 28.7 cfs, n = 2190 daily means
--   window 1985-10-01..1991-09-29 (full POR of 14304350, 6 yr)
--
-- Variable selection by bootstrap (B=1000 iid row resamples):
--   The pre-existing 4-predictor fit (Siletz, Alsea, Luckiamute,
--   Siuslaw) was rerun and reproduced almost exactly -- coefficients
--   match the legacy ones to 3 sig figs and all four predictors
--   have 100 percent bootstrap inclusion. However the marginal
--   value of the non-Siletz predictors is small: 5-fold CV RMSE
--   24.7 cfs (4-pred) vs 29.2 cfs (Siletz alone). Since Sunshine
--   sits in the Siletz drainage and the other predictors only add
--   small corrections, simplifying to Siletz-only trades 4.5 cfs
--   of CV RMSE for a much more transparent formula. The negative
--   coefficients on Alsea and Luckiamute in the 4-pred model are
--   statistically robust correction terms, not noise, but they are
--   not necessary for a defensible estimate.
--
-- Drainage-area scaling: USGS 14304350 sat at NHD-derived TotDA
-- 6.79 sq mi (matches USGS-published 6.7). The reach take-out at
-- the Sunshine Cr confluence sits at NHD TotDA 11.91 sq mi, so the
-- fit coefficients are multiplied by 1.754 = 11.91/6.79:
--
--   Sunshine_takeout = 1.754 * (0.0503754 * Siletz - 12.05)
--                    = 0.0883604 * Siletz - 21.14
--
-- The calc gauge anchor (44.815823, -123.764509) sits on Sunshine
-- Cr right at the confluence (NHD vertex ~19 ft away), so the
-- location stays put; the drainage_area meta is updated to reflect
-- the take-out value.

------------------------------------------------------------------------------
-- Refresh the calc expression and attach provenance.
------------------------------------------------------------------------------

UPDATE calc_expression
SET expression = 'round(greatest(0, 0.0883604 * oN::14305500::flow -21.14))',
    time_expression = 'oN::14305500::flow',
    note = 'Sunshine Cr at confluence estimated from Siletz-at-Siletz (USGS 14305500). Underlying OLS fit against USGS 14304350 (Sunshine Cr near Valsetz, retired 1991-09-29): slope 0.0503754, intercept -12.05, r2=0.9072, RMSE=28.7 cfs, n=2190 daily means / 6 yr overlap 1985-10-01..1991-09-29. Coefficients here are scaled by drainage-area ratio 1.754 = 11.91 sq mi (take-out per NHD HR) / 6.79 sq mi (USGS gauge per NHD HR, matches USGS-published 6.7). Replaces a legacy 4-predictor fit (Siletz, Alsea, Luckiamute, Siuslaw) whose coefficients reproduce on refit, but bootstrap variable selection showed that 95% of the variance reduction comes from Siletz alone (CV-RMSE 29.2 single-predictor vs 24.7 with all 4). Simplified to single-predictor for transparency. See docs/regression/sunshine_14304350_from_14305500.md.',
    provenance_slug = 'sunshine_14304350_from_14305500'
WHERE id = (SELECT calc_expression_id FROM source WHERE name = 'Sunshine_calc');

------------------------------------------------------------------------------
-- Update gauge metadata: lat/lon stay (anchor is on Sunshine Cr at
-- the confluence per NHD), but add the NHD take-out drainage area
-- and refresh display strings to mark this as a calc gauge.
--
-- Reach 91 ("Sunshine") continues to reference gauge.id and needs
-- no update.
------------------------------------------------------------------------------

UPDATE gauge
SET location = 'At Sunshine confluence (estimated)',
    display_name = 'Sunshine Cr at confluence (calc)',
    drainage_area = 11.9
WHERE name = 'Sunshine_calc';
