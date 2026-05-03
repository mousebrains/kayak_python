-- Migration 0012: drop reach.UNIQUE(name), replace with partial unique index
--
-- ``UNIQUE (name)`` on a nullable column is a SQLite footgun: NULLs are
-- treated as distinct, so the constraint silently permits any number of
-- unnamed rows (today: 34/389). The intent is "if name is set it must be
-- unique" — exactly what a partial unique index expresses.
--
-- SQLite has no ALTER TABLE DROP CONSTRAINT, so a table rebuild is
-- required. Other tables (reach_state, reach_class, reach_guidebook) FK to
-- reach.id with ON DELETE CASCADE; the rebuild is safe inside a single
-- transaction with foreign_keys=ON because (a) DROP TABLE does not fire
-- cascades, and (b) we re-create the table with the same id values, so
-- references resolve again at COMMIT.

CREATE TABLE reach_new (
    id INTEGER NOT NULL,
    updated_at DATETIME,
    gauge_id INTEGER,
    name VARCHAR(64),
    display_name TEXT,
    sort_name VARCHAR(256),
    nature TEXT,
    description TEXT,
    difficulties TEXT,
    basin TEXT,
    basin_area FLOAT,
    elevation FLOAT,
    elevation_lost FLOAT,
    length FLOAT,
    gradient FLOAT,
    features TEXT,
    latitude NUMERIC(9, 6),
    longitude NUMERIC(9, 6),
    latitude_start NUMERIC(9, 6),
    longitude_start NUMERIC(9, 6),
    latitude_end NUMERIC(9, 6),
    longitude_end NUMERIC(9, 6),
    no_show BOOLEAN DEFAULT 0 NOT NULL,
    notes TEXT,
    optimal_flow FLOAT,
    region TEXT,
    remoteness TEXT,
    scenery TEXT,
    season TEXT,
    watershed_type TEXT,
    aw_id INTEGER,
    river TEXT,
    max_gradient FLOAT,
    geom TEXT,
    huc TEXT,
    map_only BOOLEAN NOT NULL DEFAULT 0,
    no_flow_range BOOLEAN DEFAULT 0 NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(gauge_id) REFERENCES gauge (id) ON DELETE SET NULL
);

INSERT INTO reach_new
    SELECT id, updated_at, gauge_id, name, display_name, sort_name, nature,
           description, difficulties, basin, basin_area, elevation,
           elevation_lost, length, gradient, features, latitude, longitude,
           latitude_start, longitude_start, latitude_end, longitude_end,
           no_show, notes, optimal_flow, region, remoteness, scenery,
           season, watershed_type, aw_id, river, max_gradient, geom, huc,
           map_only, no_flow_range
      FROM reach;

DROP TABLE reach;

ALTER TABLE reach_new RENAME TO reach;

CREATE INDEX ix_reach_sort_name ON reach (sort_name);
CREATE UNIQUE INDEX ix_reach_name_unique ON reach (name) WHERE name IS NOT NULL;
