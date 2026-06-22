-- Migration 0078: change_request_bridge runtime table (editor → kayak_data PR bridge)
--
-- Tier 1 of docs/PLAN_editor_pr_bridge.md. One row per endorsed change_request,
-- tracking the worker's progress turning the frozen applied_json diff into a
-- kayak_data pull request (state machine: queued -> pr_open -> merged -> deployed,
-- plus pr_closed / conflict / worker_error). Keeps change_request.status coarse
-- (pending/approved/rejected/resolved) instead of sprawling worker state into it.
--
-- change_request_bridge is NOT dataset metadata: it is engine runtime only — never
-- exported to / synced from the dataset CSVs (absent from layout.CONTRACT_CSVS),
-- one row per change_request id, CASCADE-deleted with its request. Like fetch_state
-- (migration 0076), it is a schema-only addition; no data is written here (migrations
-- > 0074 are schema-only). The worker populates it at runtime.
--
-- DDL mirrors SQLAlchemy's Base.metadata.create_all() output for ChangeRequestBridge
-- (separate PRIMARY KEY / UNIQUE / FOREIGN KEY clauses, not the inline rowid-alias
-- form) so the migrated schema and the ORM introspect identically — keeps
-- tests/test_db/test_schema_parity.py green.
CREATE TABLE change_request_bridge (
	id INTEGER NOT NULL,
	change_request_id INTEGER NOT NULL,
	state VARCHAR(12) DEFAULT 'queued' NOT NULL,
	attempt INTEGER DEFAULT '1' NOT NULL,
	base_dataset_sha VARCHAR(40),
	reviewed_base_json TEXT,
	applied_json_sha256 VARCHAR(64),
	branch_name VARCHAR(255),
	pr_number INTEGER,
	pr_url TEXT,
	pr_head_sha VARCHAR(40),
	pr_merge_sha VARCHAR(40),
	queued_by INTEGER,
	queued_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	last_error TEXT,
	conflict_json TEXT,
	lease_owner VARCHAR(128),
	lease_expires_at DATETIME,
	heartbeat_at DATETIME,
	PRIMARY KEY (id),
	UNIQUE (change_request_id),
	FOREIGN KEY(change_request_id) REFERENCES change_request (id) ON DELETE CASCADE,
	FOREIGN KEY(queued_by) REFERENCES editor (id) ON DELETE SET NULL
);
CREATE INDEX ix_change_request_bridge_state ON change_request_bridge (state);
CREATE INDEX ix_change_request_bridge_queued_by ON change_request_bridge (queued_by);
