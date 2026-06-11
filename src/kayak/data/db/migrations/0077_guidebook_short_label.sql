-- Migration 0077: guidebook.short_label
--
-- Dataset-owned guidebook chips for search results (dataset-separation S3).
-- The column is nullable and optional in guidebook.csv for one expand release:
-- current datasets keep rendering with the legacy engine fallback until they
-- add explicit labels, then a later contract step can remove that fallback.
ALTER TABLE guidebook ADD COLUMN short_label VARCHAR(32);
