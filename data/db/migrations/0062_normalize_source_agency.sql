-- Normalize source.agency: replace parser slugs and null calc-source agencies
-- with the canonical agency name, so `GROUP BY agency` stops splitting one
-- agency across a name and a slug. review-3 R6.3 (name-map approved 2026-05-25).
--
-- Touches ONLY the mislabeled rows. Correctly-tagged sources of the same parser
-- are left as-is: e.g. the nwps parser feeds gauges already tagged NWS (72),
-- NWRFC (11), and NOAA (1) in source.csv — only the 5 that fell back to the raw
-- "nwps" slug are relabeled. `nwps`→`NWS` because NWPS is an NWS service.
--
-- Idempotent: once normalized, no row matches the slug predicates again.
UPDATE source SET agency = 'NWS'    WHERE agency = 'nwps';
UPDATE source SET agency = 'WA DOE' WHERE agency = 'wa.gov';
UPDATE source SET agency = 'NWRFC'  WHERE agency LIKE 'nwrfc.%';
UPDATE source SET agency = 'Calculation'
    WHERE calc_expression_id IS NOT NULL AND agency IS NULL;
