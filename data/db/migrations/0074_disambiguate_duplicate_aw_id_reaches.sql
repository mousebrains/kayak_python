-- Migration 0074: disambiguate the 4 duplicate-aw_id reach pairs.
--
-- Four AW run ids each map to two distinct DB reaches (5021: Molalla;
-- 10414: Siuslaw; 1561: North Umpqua; 3064: White Salmon). With reach.name
-- now the symbolic key, give each of the 8 a descriptive name so the pairs
-- are unambiguous (replacing two provisional `aw_<id>` names 0073 assigned to
-- the formerly-nameless 305/389, and the legacy gauge-code names on the rest).
-- Maintainer-supplied names. Keyed on the current (unique) name → idempotent.

UPDATE reach SET name = 'Molalla_Turner' WHERE name = 'CANO';
UPDATE reach SET name = 'Molalla_Glen_Avon' WHERE name = 'Molalla ae';
UPDATE reach SET name = 'Siuslaw_Smith_Creek' WHERE name = 'MPLO3';
UPDATE reach SET name = 'N_Umpqua_Boulder_Flat' WHERE name = 'NUMO';
UPDATE reach SET name = 'N_Umpqua_Gravel_Bin' WHERE name = 'GLIO';
UPDATE reach SET name = 'White_Salmon_BZ' WHERE name = 'aw_3064';
UPDATE reach SET name = 'Siuslaw_Clay_Creek' WHERE name = 'aw_10414';
UPDATE reach SET name = 'White_Salmon_Husum' WHERE name = 'White_Salmon_NW_Park';
