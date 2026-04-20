-- Migration 0001: baseline
--
-- Marks the schema state at the point the migration system was adopted.
-- No-op — `levels init-db` creates the baseline tables via SQLAlchemy's
-- metadata.create_all(), and `levels migrate` automatically stamps every
-- migration up through the current file set on fresh DBs. Existing
-- deployments must run `levels migrate --stamp 0001` once to adopt the
-- tracking table without re-running schema creation.
SELECT 1;
