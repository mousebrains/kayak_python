-- Migration 0011: latest_observation.source_id ON DELETE CASCADE
--
-- Was RESTRICT, which was asymmetric with latest_gauge_observation
-- (gauge_id CASCADE / source_id SET NULL) and forced manual fk-violation
-- cleanup whenever a source was removed. The cache row is rebuilt by the
-- next pipeline tick, so cascade-on-delete is the correct semantic.
--
-- SQLite has no DROP/ALTER for table-level FK constraints — full table
-- rebuild is required. No other table FKs to latest_observation, so this
-- is safe inside a single transaction with foreign_keys=ON.

CREATE TABLE latest_observation_new (
    source_id INTEGER NOT NULL,
    data_type VARCHAR(11) NOT NULL,
    observed_at DATETIME NOT NULL,
    value FLOAT NOT NULL,
    prev_observed_at DATETIME,
    prev_value FLOAT,
    delta_per_hour FLOAT,
    PRIMARY KEY (source_id, data_type),
    FOREIGN KEY(source_id) REFERENCES source (id) ON DELETE CASCADE
);

INSERT INTO latest_observation_new
    SELECT source_id, data_type, observed_at, value,
           prev_observed_at, prev_value, delta_per_hour
      FROM latest_observation;

DROP TABLE latest_observation;

ALTER TABLE latest_observation_new RENAME TO latest_observation;
