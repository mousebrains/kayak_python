-- Migration 0002: reach.no_flow_range
--
-- Adds a boolean flag on `reach` distinguishing "reviewed, no reliable
-- flow range available" from "not yet reviewed". Populated by operator
-- action; surfaced by the "reaches without flow range" audit query.
ALTER TABLE reach ADD COLUMN no_flow_range BOOLEAN DEFAULT 0 NOT NULL;
