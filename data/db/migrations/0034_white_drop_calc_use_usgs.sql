-- Migration 0034: retire White_Oregon_calc and rely on live USGS 14101500.
--
-- Gauge 134 was originally created from USGS 14101500's site metadata
-- (lat 45.241478, lon -121.095022, elevation 870.15 ft, drainage 417
-- sq mi, HUC12 170703060908 -- all identical to the USGS site row)
-- and given a 4-predictor calc_expression because 14101500 was retired
-- from 1990-09-30 through 2019-10-03 (~29 years dark).
--
-- 14101500 has been actively reporting since 2019-10-03 (with one
-- 2021-10-17..2023-05-18 outage of ~1.5 yr), and the USGS source for
-- the gauge was wired up at some point during that window (source 284,
-- name '14101500', agency 'USGS'). The calc has been running in
-- parallel; current values agree well (163 cfs calc vs 165 cfs USGS).
--
-- This migration drops the now-redundant calc machinery: the
-- 'White_Oregon_calc' source row (id 124), its observation history,
-- its gauge_source link to gauge 134, and calc_expression row 8.
-- gauge 134 itself stays put (reach 235 "aw_1564" references it by
-- id) and continues to receive USGS data via the still-linked
-- source 284. The legacy gauge.name = 'White_Oregon_calc' is kept
-- unchanged -- nothing in the repo references the name string and
-- changing it adds risk for no observable benefit (the static build
-- and PHP layer both key off ids).
--
-- No observation re-pointing: source 124's history starts 2026-02-04
-- and entirely overlaps with the authoritative USGS 14101500 series
-- (source 284), so the calc-derived observations would duplicate-by-
-- PK and add no information.

-- observation.source_id is ON DELETE RESTRICT, so clear the dead rows first.
DELETE FROM observation WHERE source_id = 124;

-- gauge_source and latest_observation cascade on source delete.
-- source.calc_expression_id has ON DELETE SET NULL but no other source
-- references calc_expression 8, so the cascade-driven NULL on the row
-- we're about to delete is harmless.
DELETE FROM source WHERE id = 124;

-- Drop the now-orphaned calc_expression row.
DELETE FROM calc_expression WHERE id = 8;
