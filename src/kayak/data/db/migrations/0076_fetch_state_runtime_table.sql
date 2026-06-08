-- Migration 0076: move fetch_url.last_fetched_at -> runtime fetch_state table
--
-- dataset-separation SA / acceptance criterion #6: engine runtime must not
-- mutate dataset-owned metadata tables, and operational timestamps belong in
-- runtime tables. `fetch_url` is dataset-owned (projected from the dataset's
-- sources.yaml -> fetch_url.csv), yet `levels fetch` wrote `last_fetched_at`
-- into it on every run. Relocate that timestamp to a new runtime-only table.
--
-- fetch_state is NOT dataset metadata: it is never exported to / synced from the
-- dataset CSVs (absent from layout.CONTRACT_CSVS), one row per fetch_url id,
-- CASCADE-deleted with its URL.
--
-- The old `last_fetched_at` value is NOT carried over (migrations are
-- schema-only; INSERT/UPDATE is forbidden for versions > 0074). It is pure
-- churn with no readers, so dropping it is harmless — `levels fetch`
-- repopulates fetch_state on the next run.
--
-- `last_fetched_at` is in no index and no foreign key, and prod runs SQLite
-- >= 3.40 (Debian 12+/13; DROP COLUMN landed in 3.35), so the plain
-- ALTER TABLE ... DROP COLUMN is safe (no 12-step table rebuild needed).

-- DDL mirrors SQLAlchemy's Base.metadata.create_all() output for FetchState
-- (separate PRIMARY KEY / FOREIGN KEY clauses, not the inline `INTEGER PRIMARY
-- KEY` rowid-alias form) so the migrated schema and the ORM introspect
-- identically — keeps tests/test_db/test_schema_parity.py green.
CREATE TABLE fetch_state (
	fetch_url_id INTEGER NOT NULL,
	last_fetched_at DATETIME,
	PRIMARY KEY (fetch_url_id),
	FOREIGN KEY(fetch_url_id) REFERENCES fetch_url (id) ON DELETE CASCADE
);

ALTER TABLE fetch_url DROP COLUMN last_fetched_at;
