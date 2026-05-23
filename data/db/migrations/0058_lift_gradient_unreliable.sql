-- Migration 0058: lift gradient_unreliable on 9 reaches that the
-- DL_MI=0.2 mi algorithm now handles cleanly.
--
-- Background: these reaches were flagged gradient_unreliable=1 under
-- the earlier DL_MI=0.0625 algorithm, where spiky single-bin DEM
-- artifacts (canyon-wall strand-hopping, bridge over-passes, mis-snap
-- onto adjacent terrain) pushed isolated bars above the 1000 ft/mi
-- extreme-peak threshold and produced visibly bad gradient profile
-- charts. Bumping DL_MI to 0.2 (commit 6de0032) bins those artifacts
-- with their neighbors and the noise washes out.
--
-- Re-running the current bin-mean + cumsum algorithm on these 9
-- reaches now puts every peak between 110 and 632 ft/mi — all well
-- below the 1000 ft/mi warning threshold and visually plausible for
-- the river class (Class V drops on McKenzie 244 / Butte Creek 262
-- / Salmon 251 reach 500-650 ft/mi peaks; the rest are big rivers
-- under 250 ft/mi).
--
-- max_gradient + gradient_profile for these rows are populated by
-- the next regenerated 0046 backfill (same commit). This migration
-- only flips the flag; idempotent on the WHERE.

UPDATE reach SET gradient_unreliable = 0
 WHERE id IN (117, 127, 155, 244, 251, 262, 299, 314, 405)
   AND gradient_unreliable = 1;
