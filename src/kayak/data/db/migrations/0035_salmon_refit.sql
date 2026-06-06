-- Migration 0035: refit SALMON_WILLAMETTE_calc with a single-predictor
-- OLS against USGS 14146500 (Salmon Cr near Oakridge, retired 1994),
-- and relocate the calc gauge meta onto that historical site.
--
-- Underlying OLS fit (docs/regression/salmon_14146500_from_nfmf):
--   Salmon_at_14146500 = 0.439612 * NFMF + 78.71
--   r2 = 0.9524, RMSE = 84.5 cfs, n = 21958 daily means
--   window 1913-02-01..1994-06-13 (full POR of USGS 14146500, ~60 yr
--   pairwise overlap with NFMF)
--
-- A 2-predictor fit adding USGS 14144900 (Hills Cr above Hills Cr
-- Reservoir) was explored: r2 = 0.9528, RMSE = 88.0, n = 8400 over
-- 1958-1981. Both predictors were statistically significant
-- (bootstrap B=1000, 100 percent inclusion frequency) and the 2-pred
-- CV-RMSE was marginally better than NFMF-only over the shared
-- window. HOWEVER 14144900's flow record (parameter 00060) actually
-- ENDED 1981-09-29 -- the seriesCatalog "end_date" of 2026-05-17
-- refers to the gauge-height (00065) series. USGS no longer
-- publishes real-time flow for this gauge, so it cannot drive a
-- live calc_expression. Dropped; using the single-predictor fit
-- over the full 1913-1994 NFMF overlap instead. With 2.6x more
-- training data the NFMF-only RMSE (84.5) actually beats the
-- 2-predictor RMSE (88.0).
--
-- The legacy expression (Salmon = NFMF / 3) underpredicted by ~166 cfs
-- on average against the historical record (RMSE 214 cfs vs target
-- mean 425 cfs) -- it was a drainage-area-ratio guess, not a fit.
--
-- No drainage-area scaling: the calc gauge is being moved onto the
-- USGS 14146500 mid-reach site (DA 117 sq mi). The reach take-out
-- near Oakridge is ~6 mi further downstream at the mouth of Salmon
-- Cr; the calc now honestly reports mid-reach flow rather than
-- implying it represents the take-out.

------------------------------------------------------------------------------
-- Refresh the calc expression and attach provenance.
------------------------------------------------------------------------------

UPDATE calc_expression
SET expression = 'round(greatest(0, 0.439612 * fl::14147500::flow +78.71))',
    time_expression = 'fl::14147500::flow',
    note = 'Salmon Cr near Oakridge estimated from NFMF Willamette (USGS 14147500) via OLS fit against USGS 14146500 (Salmon Cr near Oakridge, retired 1994-06-13). n=21958 daily means / ~60 yr overlap 1913-02-01..1994-06-13, r2=0.9524, RMSE=84.5 cfs. Hills Cr above Hills Cr Reservoir (USGS 14144900) was tested as a second predictor and rejected: real-time flow stopped publishing 1981-09-29 even though gauge-height continues, so 14144900 cannot drive a live calc. Replaces a legacy Salmon = NFMF/3 drainage-area-ratio guess that underpredicted by ~166 cfs on average. Calc gauge co-located with the historical USGS site (mid-reach, ~6 mi upstream of the reach take-out), so no drainage-area scaling applied. See docs/regression/salmon_14146500_from_nfmf.md.',
    provenance_slug = 'salmon_14146500_from_nfmf'
WHERE id = (SELECT calc_expression_id FROM source WHERE name = 'SALMON_WILLAMETTE_calc');

------------------------------------------------------------------------------
-- Relocate SALMON_WILLAMETTE_calc gauge metadata onto USGS 14146500.
--
--   USGS 14146500 "SALMON CREEK NEAR OAKRIDGE,OREG.":
--     lat 43.762346, lon -122.372823
--     elevation 1462.36 ft, drainage area 117 sq mi
--     HUC12 170900010403 Lower Salmon Creek (unchanged -- both old
--     and new lat/lon sit in this same HUC12)
--
-- The pre-existing row sat at 43.740821, -122.458827 (near the reach
-- take-out at the mouth of Salmon Cr, ~6 mi WSW of the gauge). Reach
-- 73 ("Salmon an", 8.7 mi) continues to reference gauge.id and needs
-- no update; the calc now reports flow at the mid-reach gauge point
-- rather than implying it represents the take-out.
--
-- sort_name updated to match the new elevation (token 008538 = 10000
-- minus elevation_ft rounded, matching the basin convention) and now
-- carries the drainage area (was 999999 for unknown).
------------------------------------------------------------------------------

UPDATE gauge
SET latitude = 43.762346,
    longitude = -122.372823,
    location = 'Near Oakridge (estimated)',
    display_name = 'Salmon Cr near Oakridge (calc)',
    elevation = 1462.36,
    drainage_area = 117.0,
    sort_name = 'salmon creek|9|008538|000117'
WHERE name = 'SALMON_WILLAMETTE_calc';
