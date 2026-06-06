-- Migration 0031: refresh SF_ALSEA_calc with explicit provenance, and
-- relocate the calc gauge to the historical USGS 14306200 site (S Fork
-- Alsea River near Alsea, OR), which sat near the SF / main Alsea
-- confluence.
--
-- Underlying OLS fit (docs/regression/sf_alsea_14306200_from_14306500):
--   SF_Alsea = 0.110684 * 14306500 - 1.406
--   r2 = 0.9649, RMSE = 47.9 cfs, n = 1094 daily means
--   window 1960-10-01..1963-09-29 (full period of record of 14306200,
--   only ~3 years -- 14306200 was a short-lived USGS site)
--
-- The pre-existing expression (0.1106834 * Alsea - 1.403748) matches
-- this fit to four significant figures, just like the NF Alsea calc
-- did before migration 0030 -- whoever introduced the calc did the
-- regression but left no note. This migration locks the expression
-- to the freshly-fit coefficients and attaches the writeup.
--
-- No drainage-area scaling: the calc gauge is being moved onto the
-- historical USGS gauge position (near the confluence with the main
-- Alsea), so the fit's target location and the calc anchor are
-- identical -- no DA ratio to apply. (The pre-existing calc anchor
-- was already within ~300 ft of the historical site, so this is a
-- microscopic shift -- mostly a meta refresh.)
--
-- Why match calc rows by joining through source.name: time_expression
-- ('q::14306500::flow') is shared by calcs 2, 4, 6 (DRIFT_ALSEA,
-- NF_ALSEA, SF_ALSEA all consume Alsea-near-Tidewater), so it is not a
-- unique key. The source row pointing at the calc *is* unique.

------------------------------------------------------------------------------
-- Refresh the regression formula and attach provenance.
------------------------------------------------------------------------------

UPDATE calc_expression
SET expression = 'round(greatest(0, 0.110684 * q::14306500::flow -1.406))',
    note = 'SF Alsea estimated from Alsea-near-Tidewater (USGS 14306500) via OLS fit against USGS 14306200 (S Fork Alsea River near Alsea, retired 1963-09-29). n=1094 daily means / ~3 yr overlap 1960-10-01..1963-09-29, r2=0.9649, RMSE=47.9 cfs. Short period of record (only three particular wet/dry seasons sampled). Heteroscedastic at high flow (Q5 residual std 103 cfs). Calc gauge co-located with the historical USGS site near the SF and main Alsea confluence, so no drainage-area scaling applied. See docs/regression/sf_alsea_14306200_from_14306500.md.',
    provenance_slug = 'sf_alsea_14306200_from_14306500'
WHERE id = (SELECT calc_expression_id FROM source WHERE name = 'SF_ALSEA_calc');

------------------------------------------------------------------------------
-- Relocate SF_ALSEA_calc gauge metadata onto USGS 14306200 ground truth.
--
--   USGS 14306200 "S FK ALSEA R NR ALSEA, OREG.":
--     lat 44.365121, lon -123.599827
--     elevation 300.00 ft, drainage area 49.50 sq mi
--     HUC8 17100205; HUC12 171002050104 (Lower South Fork Alsea River,
--     same HUC12 as the pre-existing calc location)
--
-- The pre-existing row sat at 44.365887,-123.599478, ~300 ft NE of the
-- gauge -- effectively the same site, but the metadata never carried
-- the USGS-published elevation or drainage area. Reach 85 ("Alsea b")
-- continues to reference this gauge by id and needs no update.
------------------------------------------------------------------------------

UPDATE gauge
SET latitude = 44.365121,
    longitude = -123.599827,
    location = 'Near Alsea (estimated)',
    display_name = 'SF Alsea near Alsea (calc)',
    elevation = 300.0,
    drainage_area = 49.5,
    huc = '171002050104'
WHERE name = 'SF_ALSEA_calc';
