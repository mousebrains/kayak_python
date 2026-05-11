-- Migration 0018: drop 19 dead source rows from 14 split-source gauges.
--
-- For each affected gauge a sibling source is still receiving data; the
-- dead row is a leftover from earlier source-splitting / OGC pipeline
-- placeholder work. Most dead rows carry zero flow observations; four
-- have meaningful history that's re-pointed onto the alive sibling via
-- INSERT OR IGNORE before the source row goes.
--
-- Mapping dead -> alive (alive = lowest source.id with recent flow obs on
-- the same gauge):
--   36 -> 37    (gauge 39  Imnaha_Imnaha_merge:        13292000 -> IMNO3)
--   130 -> 131  (gauge 139 ALBANY_merge: WILLAMETTE_RIVER_AT_ALBANY -> ALBO3)
--   146 -> 182  (gauge 150 29C100:        29C100 dup -> 29C100)
--   174 -> 200  (gauge 161 Applegate_Lake: APLO3 -> calc)
--   176 -> 177  (gauge 78  14147500:      NMFO3 dup -> NMFO3)
--   178 -> 220  (gauge 184 28B080:        WASW1 -> 28B080)
--   181 -> 182  (gauge 150 29C100:        29C100 dup -> 29C100)
--   197 -> 198  (gauge 174 Fall_Creek_Inflow: FALO3 -> calc)
--   218 -> 220  (gauge 184 28B080:        28B080 dup -> 28B080)
--   219 -> 220  (gauge 184 28B080:        28B080 dup -> 28B080)
--   227 -> 4    (gauge 5   Blue_Tidbits_merge:   14161100 -> BRTO3)
--   237 -> 22   (gauge 24  Deschutes_Benham_merge: 14064500 -> BENO)
--   238 -> 24   (gauge 26  Deschutes_Wickiup_merge: 14056500 -> WICO)
--   258 -> 177  (gauge 78  14147500:      14147500 placeholder -> NMFO3)
--   261 -> 82   (gauge 83  Payette_Cascade_merge: 13245000 -> CSCI)
--   275 -> 153  (gauge 120 Snake_Hells_Canyon_merge: 13290450 -> HCDI)
--   278 -> 117  (gauge 125 Tualatin_Farmington_merge: 14206500 -> FRMO3)
--   283 -> 123  (gauge 133 WFHO3:         14118500 -> WFHO3)
--   291 -> 200  (gauge 161 Applegate_Lake: 14361900 -> calc)
--
-- Calc expressions resolve identifiers by gauge.name (see
-- src/kayak/cli/calculator.py:213-240), so dropping these source rows
-- can't break any calc — the parent gauges all survive.

-- Re-point each dead source's observations onto its alive sibling.
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 37,  observed_at, data_type, value FROM observation WHERE source_id = 36;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 131, observed_at, data_type, value FROM observation WHERE source_id = 130;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 182, observed_at, data_type, value FROM observation WHERE source_id = 146;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 200, observed_at, data_type, value FROM observation WHERE source_id = 174;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 177, observed_at, data_type, value FROM observation WHERE source_id = 176;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 220, observed_at, data_type, value FROM observation WHERE source_id = 178;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 182, observed_at, data_type, value FROM observation WHERE source_id = 181;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 198, observed_at, data_type, value FROM observation WHERE source_id = 197;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 220, observed_at, data_type, value FROM observation WHERE source_id = 218;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 220, observed_at, data_type, value FROM observation WHERE source_id = 219;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 4,   observed_at, data_type, value FROM observation WHERE source_id = 227;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 22,  observed_at, data_type, value FROM observation WHERE source_id = 237;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 24,  observed_at, data_type, value FROM observation WHERE source_id = 238;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 177, observed_at, data_type, value FROM observation WHERE source_id = 258;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 82,  observed_at, data_type, value FROM observation WHERE source_id = 261;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 153, observed_at, data_type, value FROM observation WHERE source_id = 275;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 117, observed_at, data_type, value FROM observation WHERE source_id = 278;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 123, observed_at, data_type, value FROM observation WHERE source_id = 283;
INSERT OR IGNORE INTO observation (source_id, observed_at, data_type, value)
    SELECT 200, observed_at, data_type, value FROM observation WHERE source_id = 291;

-- observation.source_id is ON DELETE RESTRICT, so clear the dead rows first.
DELETE FROM observation WHERE source_id IN (
    36, 130, 146, 174, 176, 178, 181, 197, 218, 219,
    227, 237, 238, 258, 261, 275, 278, 283, 291
);

-- gauge_source and latest_observation cascade.
DELETE FROM source WHERE id IN (
    36, 130, 146, 174, 176, 178, 181, 197, 218, 219,
    227, 237, 238, 258, 261, 275, 278, 283, 291
);
