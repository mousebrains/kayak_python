-- Migration 0030: refresh NF_ALSEA_calc with explicit provenance, and
-- relocate the calc gauge to the historical USGS 14306100 site (NF
-- Alsea at Alsea, OR), which sat at the NF / main Alsea confluence.
--
-- Underlying OLS fit (docs/regression/nf_alsea_14306100_from_14306500):
--   NF_Alsea = 0.194065 * 14306500 - 9.519
--   r2 = 0.9563, RMSE = 96.3 cfs, n = 11687 daily means
--   window 1957-10-01..1989-09-29 (full period of record of 14306100)
--
-- The pre-existing expression already matched this fit to four significant
-- figures (slope identical, intercept -9.517 vs -9.519); whoever introduced
-- the calc did the regression but left no note. This migration locks the
-- expression to the freshly-fit coefficients and attaches the writeup.
--
-- No drainage-area scaling: the calc gauge is being moved onto the
-- historical USGS gauge position (at the confluence with the main
-- Alsea), so the fit's target location and the calc anchor are
-- identical -- no DA ratio to apply.
--
-- Why match calc rows by joining through source.name: time_expression
-- ('q::14306500::flow') is shared by calcs 2, 4, 6 (DRIFT_ALSEA,
-- NF_ALSEA, SF_ALSEA all consume Alsea-near-Tidewater), so it is not a
-- unique key. The source row pointing at the calc *is* unique.

------------------------------------------------------------------------------
-- Refresh the regression formula and attach provenance.
------------------------------------------------------------------------------

UPDATE calc_expression
SET expression = 'round(greatest(0, 0.194065 * q::14306500::flow -9.519))',
    note = 'NF Alsea estimated from Alsea-near-Tidewater (USGS 14306500) via OLS fit against USGS 14306100 (NF Alsea at Alsea, retired 1989-09-29). n=11687 daily means / ~32 yr overlap 1957-10-01..1989-09-29, r2=0.9563, RMSE=96.3 cfs. Slope window-stable (0.188-0.194 across 17-32 yr sub-windows). Heteroscedastic at high flow (Q5 residual std 209 cfs). Calc gauge co-located with the historical USGS site at the NF and main Alsea confluence, so no drainage-area scaling applied. See docs/regression/nf_alsea_14306100_from_14306500.md.',
    provenance_slug = 'nf_alsea_14306100_from_14306500'
WHERE id = (SELECT calc_expression_id FROM source WHERE name = 'NF_ALSEA_calc');

------------------------------------------------------------------------------
-- Relocate NF_ALSEA_calc gauge metadata onto USGS 14306100 ground truth.
--
--   USGS 14306100 "North Fork Alsea River at Alsea, OR":
--     lat 44.3790098, lon -123.5956608
--     elevation 272.31 ft, drainage area 63 sq mi
--     HUC8 17100205; HUC12 171002050105 (Lower North Fork Alsea River)
--
-- The pre-existing row sat at 44.416192,-123.561792 (HUC12 171002050103,
-- Upper NF Alsea), ~2.5 mi upstream of the gauge, and carried no
-- drainage_area. Reaches 58 ("NF Alsea") and 217 ("aw_1484") continue
-- to reference this gauge by id and need no update.
------------------------------------------------------------------------------

UPDATE gauge
SET latitude = 44.379010,
    longitude = -123.595661,
    location = 'At Alsea (estimated)',
    display_name = 'NF Alsea at Alsea (calc)',
    elevation = 272.31,
    drainage_area = 63.0,
    huc = '171002050105'
WHERE name = 'NF_ALSEA_calc';
