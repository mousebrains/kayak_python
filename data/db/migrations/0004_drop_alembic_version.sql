-- Migration 0004: drop vestigial Alembic tracking table.
--
-- `alembic_version` was seeded by an Alembic bootstrap that never ran any
-- real migrations — the custom `levels migrate` + schema_migrations table
-- replaced it. No SQLAlchemy model references it, so fresh DBs created via
-- Base.metadata.create_all() never produce the table; only pre-migration
-- live DBs still carry a single row.
DROP TABLE IF EXISTS alembic_version;
