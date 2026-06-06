-- Migration 0073: name the 34 reaches that still have a NULL reach.name.
--
-- reach.name is becoming the symbolic foreign key (and the unique handle) in
-- the metadata-single-source redesign, so every reach needs a unique name.
-- 31 take the American Whitewater id as `aw_<aw_id>`; three are hand-named by
-- the maintainer (304 has no aw_id; 306 + 389 prefer a descriptive handle, and
-- 389 would otherwise collide with reach 297 which already holds `aw_3064`).
--
-- Keyed portably + idempotently: aw_id + `name IS NULL` targets exactly the
-- still-nameless row (a duplicate aw_id whose sibling is already named is
-- excluded by the NULL guard); reach 304 is the lone aw_id-less nameless reach.
-- A from-scratch rebuild loads the names from reach.csv (migration stamped).

UPDATE reach SET name = 'SF_McKenzie_lower' WHERE aw_id IS NULL AND name IS NULL;
UPDATE reach SET name = 'aw_10414' WHERE aw_id = 10414 AND name IS NULL;
UPDATE reach SET name = 'S_Umpqua_Picket_2_Lawson' WHERE aw_id = 3768 AND name IS NULL;
UPDATE reach SET name = 'aw_3461' WHERE aw_id = 3461 AND name IS NULL;
UPDATE reach SET name = 'aw_10450' WHERE aw_id = 10450 AND name IS NULL;
UPDATE reach SET name = 'aw_11770' WHERE aw_id = 11770 AND name IS NULL;
UPDATE reach SET name = 'aw_11659' WHERE aw_id = 11659 AND name IS NULL;
UPDATE reach SET name = 'aw_2871' WHERE aw_id = 2871 AND name IS NULL;
UPDATE reach SET name = 'aw_10439' WHERE aw_id = 10439 AND name IS NULL;
UPDATE reach SET name = 'aw_10446' WHERE aw_id = 10446 AND name IS NULL;
UPDATE reach SET name = 'aw_11721' WHERE aw_id = 11721 AND name IS NULL;
UPDATE reach SET name = 'aw_11031' WHERE aw_id = 11031 AND name IS NULL;
UPDATE reach SET name = 'aw_2728' WHERE aw_id = 2728 AND name IS NULL;
UPDATE reach SET name = 'aw_10409' WHERE aw_id = 10409 AND name IS NULL;
UPDATE reach SET name = 'aw_10413' WHERE aw_id = 10413 AND name IS NULL;
UPDATE reach SET name = 'aw_1521' WHERE aw_id = 1521 AND name IS NULL;
UPDATE reach SET name = 'aw_10819' WHERE aw_id = 10819 AND name IS NULL;
UPDATE reach SET name = 'aw_2709' WHERE aw_id = 2709 AND name IS NULL;
UPDATE reach SET name = 'aw_2711' WHERE aw_id = 2711 AND name IS NULL;
UPDATE reach SET name = 'aw_3076' WHERE aw_id = 3076 AND name IS NULL;
UPDATE reach SET name = 'aw_3077' WHERE aw_id = 3077 AND name IS NULL;
UPDATE reach SET name = 'aw_3542' WHERE aw_id = 3542 AND name IS NULL;
UPDATE reach SET name = 'aw_2270' WHERE aw_id = 2270 AND name IS NULL;
UPDATE reach SET name = 'aw_2156' WHERE aw_id = 2156 AND name IS NULL;
UPDATE reach SET name = 'aw_3510' WHERE aw_id = 3510 AND name IS NULL;
UPDATE reach SET name = 'aw_3511' WHERE aw_id = 3511 AND name IS NULL;
UPDATE reach SET name = 'aw_2273' WHERE aw_id = 2273 AND name IS NULL;
UPDATE reach SET name = 'aw_2264' WHERE aw_id = 2264 AND name IS NULL;
UPDATE reach SET name = 'aw_3513' WHERE aw_id = 3513 AND name IS NULL;
UPDATE reach SET name = 'aw_2260' WHERE aw_id = 2260 AND name IS NULL;
UPDATE reach SET name = 'aw_2261' WHERE aw_id = 2261 AND name IS NULL;
UPDATE reach SET name = 'aw_2262' WHERE aw_id = 2262 AND name IS NULL;
UPDATE reach SET name = 'aw_2263' WHERE aw_id = 2263 AND name IS NULL;
UPDATE reach SET name = 'White_Salmon_NW_Park' WHERE aw_id = 3064 AND name IS NULL;
