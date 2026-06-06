-- Migration 0019: switch source 128 (EUGO3, gauge 137 EUGENE_merge) from the
-- NWPS JSON feed to the NWRFC textPlot endpoint so we recover observed flow.
--
-- Background: USGS stopped publishing flow at station 14158050 on 2026-04-20.
-- NWPS (api.water.noaa.gov) and the legacy waterdata scraper both stopped
-- emitting flow that day, but NWRFC's textPlot pe=HG page still computes
-- discharge for EUGO3 via its local rating curve. The nwrfc.textplot parser
-- was extended to read pe=HG's paired Stage+Discharge layout, so wiring this
-- URL gives source 128 both gauge and flow without adding a new source row.
--
-- USGS-OGC source 286 (14158050) continues providing gauge + temperature, so
-- gauge data still has redundancy after this change. The old NWPS URL is
-- marked is_active=0 (not deleted) because schema_migrations/history rows
-- shouldn't lose the historical fetch_url_id reference.

INSERT OR IGNORE INTO fetch_url (parser, url, hours, is_active) VALUES (
    'nwrfc.textplot',
    'https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=EUGO3&pe=HG',
    '',
    1
);

UPDATE source SET fetch_url_id = (
    SELECT id FROM fetch_url
    WHERE url = 'https://www.nwrfc.noaa.gov/station/flowplot/textPlot.cgi?id=EUGO3&pe=HG'
)
WHERE id = 128;

UPDATE fetch_url SET is_active = 0
WHERE url = 'https://api.water.noaa.gov/nwps/v1/gauges/EUGO3/stageflow/observed';
