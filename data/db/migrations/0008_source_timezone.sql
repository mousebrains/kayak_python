-- Migration 0008: source.timezone
--
-- Per-station IANA timezone name for parsers that receive naive local-time
-- timestamps (USBR's per-station local TZ; wa.gov year-round PST). NULL
-- means "naive -> UTC at store time" (current behavior for all other
-- parsers). Populated from data/sources.yaml via sync_sources().
ALTER TABLE source ADD COLUMN timezone TEXT;
