-- Migration 0032: refit Calapooia_Holly_calc with a fresh 3-predictor
-- OLS fit + bootstrap-vetted variable selection, and relocate the calc
-- gauge metadata onto the historical USGS 14172000 site (Calapooia R
-- at Holley, retired 1990).
--
-- Underlying OLS fit (docs/regression/calapooia_14172000_from_mohawk_wiley_thomas):
--   Calapooia = 0.189876 * Mohawk + 1.11498 * Wiley + 0.152599 * Thomas + 19.26
--   r2 = 0.9846, RMSE = 80.9 cfs, n = 3592 daily means
--   window 1962-10-01..1990-09-30 (effective overlap ends 1973-07-31 due
--   to ~15 yr gaps in each predictor's daily record mid-century)
--
-- Bootstrap variable selection (B=1000 iid row resamples):
--   - Mohawk, Wiley, Thomas all 100 percent inclusion frequency,
--     tight bootstrap CIs, no sign flips.
--   - Adding USGS 14173500 (Calapooia at Albany, same river ~40 mi
--     downstream) FAILS the test: 95 percent bootstrap CI crosses
--     zero, sign goes negative in the multi-predictor model, and
--     5-fold CV RMSE gets slightly worse (82.5 vs 82.1 cfs). The
--     other three predictors already span the basin signal, so the
--     fourth gauge is redundant.
--
-- The pre-existing expression overpredicted by ~15 percent at typical
-- flows (508 cfs at predictor means vs. observed target mean 440 cfs),
-- with coefficients that don not match an OLS fit on the historical
-- record under any window or intercept-constraint choice tried. The
-- new fit reproduces the historical target mean exactly by construction.
--
-- No drainage-area scaling: the calc gauge is being co-located with
-- the historical USGS site (DA 105 sq mi at Holley), so the fit
-- target and the calc anchor are identical. The pre-existing calc
-- anchor was within ~50 m of the USGS coordinates anyway.

------------------------------------------------------------------------------
-- Refresh the regression formula and attach provenance.
------------------------------------------------------------------------------

UPDATE calc_expression
SET expression = 'round(greatest(0, 0.189876 * g8::Mohawk_Springfield_merge::flow + 1.11498 * uP::Wiley_Foster_merge::flow + 0.152599 * rq::14188800::flow +19.26))',
    time_expression = 'g8::Mohawk_Springfield_merge::flow uP::Wiley_Foster_merge::flow rq::14188800::flow',
    note = 'Calapooia at Holley estimated from Mohawk (14165000), Wiley (14187000), and Thomas (14188800) via OLS fit against USGS 14172000 (Calapooia R at Holley, retired 1990-09-30). n=3592 daily means / ~10 yr effective overlap 1962-10-01..1973-07-31 (predictor gaps truncate the nominal 28 yr window). r2=0.9846, RMSE=80.9 cfs. Sub-window stable (Mohawk 0.18-0.20, Wiley 0.98-1.12, Thomas 0.15-0.18). USGS 14173500 (Calapooia at Albany, same river downstream) was bootstrap-tested and rejected (95% CI crosses zero, CV-RMSE no improvement). Calc gauge co-located with the historical USGS site, so no drainage-area scaling applied. Replaces a legacy formula that overpredicted by ~15% at typical flows. See docs/regression/calapooia_14172000_from_mohawk_wiley_thomas.md.',
    provenance_slug = 'calapooia_14172000_from_mohawk_wiley_thomas'
WHERE id = (SELECT calc_expression_id FROM source WHERE name = 'Calapooia_Holly_calc');

------------------------------------------------------------------------------
-- Relocate Calapooia_Holly_calc gauge metadata onto USGS 14172000.
--
--   USGS 14172000 "CALAPOOIA R AT HOLLEY OREG":
--     lat 44.351236, lon -122.787307
--     elevation 527.58 ft, drainage area 105 sq mi
--     HUC8 17090003; HUC12 170900030304 (Brush Creek-Calapooia River,
--     unchanged from the pre-existing calc location which was only
--     ~50 m away)
--
-- Source.name and gauge.name remain 'Calapooia_Holly_calc' (the calc
-- expression references no name strings, but reach.gauge_id and the
-- handle convention in other migrations could). Display strings use
-- the correct USGS spelling "Holley". Reaches 265, 309, 310 continue
-- to reference this gauge by id.
------------------------------------------------------------------------------

UPDATE gauge
SET latitude = 44.351236,
    longitude = -122.787307,
    location = 'At Holley (estimated)',
    display_name = 'Calapooia at Holley (calc)',
    elevation = 527.58,
    drainage_area = 105.0
WHERE name = 'Calapooia_Holly_calc';
