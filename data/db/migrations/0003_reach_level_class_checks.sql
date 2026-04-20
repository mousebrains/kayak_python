-- Migration 0003: add low <= high CHECK constraints on reach_level and reach_class
--
-- SQLite can't ALTER TABLE ADD CONSTRAINT, so the table-recreate pattern
-- (create new, copy rows, drop old, rename) is the only path. No indexes
-- or triggers on either table, so nothing else needs restoring.
--
-- Also sweeps up any reach_level / reach_class rows pointing at deleted
-- reaches — historically FK cascade did not always fire (foreign_keys
-- PRAGMA defaults OFF for each SQLite connection), so a handful of
-- orphans can accumulate and would fail the new FK on reinsert.
DELETE FROM reach_level WHERE reach_id NOT IN (SELECT id FROM reach);
DELETE FROM reach_class WHERE reach_id NOT IN (SELECT id FROM reach);

CREATE TABLE reach_level_new (
    id INTEGER NOT NULL PRIMARY KEY,
    reach_id INTEGER NOT NULL,
    level VARCHAR(4) NOT NULL,
    low FLOAT,
    low_data_type VARCHAR(11),
    high FLOAT,
    high_data_type VARCHAR(11),
    FOREIGN KEY(reach_id) REFERENCES reach(id) ON DELETE CASCADE,
    CONSTRAINT ck_reach_level_low_le_high CHECK (low IS NULL OR high IS NULL OR low <= high)
);
INSERT INTO reach_level_new SELECT * FROM reach_level;
DROP TABLE reach_level;
ALTER TABLE reach_level_new RENAME TO reach_level;

CREATE TABLE reach_class_new (
    id INTEGER NOT NULL PRIMARY KEY,
    reach_id INTEGER NOT NULL,
    name VARCHAR(32) NOT NULL,
    low FLOAT,
    low_data_type VARCHAR(11),
    high FLOAT,
    high_data_type VARCHAR(11),
    FOREIGN KEY(reach_id) REFERENCES reach(id) ON DELETE CASCADE,
    CONSTRAINT ck_reach_class_low_le_high CHECK (low IS NULL OR high IS NULL OR low <= high)
);
INSERT INTO reach_class_new SELECT * FROM reach_class;
DROP TABLE reach_class;
ALTER TABLE reach_class_new RENAME TO reach_class;
