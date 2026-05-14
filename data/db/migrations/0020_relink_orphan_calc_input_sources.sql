-- Migration 0020: relink orphan NWRFC textPlot sources to their calc-input gauges.
--
-- Migration 0018 deleted the upstream sources feeding two `inflow_to_flow`
-- calc gauges:
--
--   gauge 161 Applegate_Lake     ← had sources 174 (APLO3/NWS) + 291 (14361900/USGS)
--   gauge 174 Fall_Creek_Inflow  ← had source 197 (FALO3)
--
-- The migration re-pointed observations onto the calc sources (200, 198) and
-- deleted the upstream rows, but a calc source has no fetcher — it produces
-- `flow` by *reading* its own gauge's `inflow`. Without a real upstream feeding
-- inflow, the calc froze on whatever data the migration last copied.
--
-- When `levels fetch` next ran against the still-active fetch_url rows for
-- APLO3 and FALO3, parsers/base.py::_auto_create_source minted fresh source
-- rows (299 APLO3, 300 FALO3) — but auto-create writes Source only, not
-- GaugeSource. The new rows have been fetching cleanly into orbit for days
-- while their gauges sat stale.
--
-- Fix: add the missing gauge_source rows so update_latest_gauge picks up the
-- live inflow, then the calc can compute fresh flow.
--
-- INSERT OR IGNORE in case the rows have been added manually before the
-- migration lands on a given DB.

INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (161, 299);
INSERT OR IGNORE INTO gauge_source (gauge_id, source_id) VALUES (174, 300);
