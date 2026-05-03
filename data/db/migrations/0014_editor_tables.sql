-- Migration 0014: editor / change_request / edit_history tables
--
-- These tables back the editor account system (Phase 1) and the change
-- request queue (Phase 2). They were already created on every fresh DB
-- via ``init-db`` → ``Base.metadata.create_all()``, but no migration file
-- documented their DDL — the schema lived only in models.py.
--
-- ``CREATE TABLE IF NOT EXISTS`` makes this safe to apply on any DB:
--   * Live prod and any existing dev DB: tables already exist, no-op.
--   * Fresh DB after ``init-db``: same — init-db creates the tables and
--     stamps every migration including this one, so it never re-runs.
--   * Hypothetical DB whose base tables exist but where the editor schema
--     has been dropped: this migration restores it.
--
-- DDL is the exact output of ``Base.metadata.create_all()`` on a fresh
-- SQLite DB (verified by diffing against ``init-db`` output for kayak.db).

CREATE TABLE IF NOT EXISTS editor (
    id INTEGER NOT NULL,
    email VARCHAR(255) NOT NULL,
    display_name VARCHAR(128),
    status VARCHAR(10) DEFAULT 'pending' NOT NULL,
    request_note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    reviewed_at DATETIME,
    reviewed_by INTEGER,
    last_login_at DATETIME,
    PRIMARY KEY (id),
    UNIQUE (email),
    FOREIGN KEY(reviewed_by) REFERENCES editor (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_editor_status ON editor (status);

CREATE TABLE IF NOT EXISTS editor_session (
    id INTEGER NOT NULL,
    editor_id INTEGER NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires_at DATETIME NOT NULL,
    last_seen_at DATETIME,
    ip VARCHAR(45),
    user_agent VARCHAR(512),
    revoked_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(editor_id) REFERENCES editor (id) ON DELETE CASCADE,
    UNIQUE (token_hash)
);
CREATE INDEX IF NOT EXISTS ix_editor_session_editor_id ON editor_session (editor_id);

CREATE TABLE IF NOT EXISTS editor_magic_link (
    id INTEGER NOT NULL,
    editor_id INTEGER NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires_at DATETIME NOT NULL,
    used_at DATETIME,
    ip_issued VARCHAR(45),
    next_url VARCHAR(512),
    PRIMARY KEY (id),
    FOREIGN KEY(editor_id) REFERENCES editor (id) ON DELETE CASCADE,
    UNIQUE (token_hash)
);
CREATE INDEX IF NOT EXISTS ix_editor_magic_link_editor_id ON editor_magic_link (editor_id);

CREATE TABLE IF NOT EXISTS maintainer_credential (
    id INTEGER NOT NULL,
    editor_id INTEGER NOT NULL,
    credential_id VARCHAR(255) NOT NULL,
    public_key TEXT NOT NULL,
    sign_count INTEGER DEFAULT 0 NOT NULL,
    transports VARCHAR(128),
    nickname VARCHAR(64),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_used_at DATETIME,
    revoked_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(editor_id) REFERENCES editor (id) ON DELETE CASCADE,
    UNIQUE (credential_id)
);
CREATE INDEX IF NOT EXISTS ix_maintainer_credential_editor_id ON maintainer_credential (editor_id);

CREATE TABLE IF NOT EXISTS change_request (
    id INTEGER NOT NULL,
    target_type VARCHAR(11) NOT NULL,
    target_id INTEGER,
    editor_id INTEGER NOT NULL,
    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    subject VARCHAR(256),
    payload_json TEXT NOT NULL,
    notes_to_maint TEXT,
    status VARCHAR(12) DEFAULT 'pending' NOT NULL,
    reviewed_at DATETIME,
    reviewed_by INTEGER,
    reviewer_note TEXT,
    applied_json TEXT,
    source_url TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(editor_id) REFERENCES editor (id) ON DELETE CASCADE,
    FOREIGN KEY(reviewed_by) REFERENCES editor (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_change_request_editor_id ON change_request (editor_id);
CREATE INDEX IF NOT EXISTS ix_change_request_target ON change_request (target_type, target_id);
CREATE INDEX IF NOT EXISTS ix_change_request_status ON change_request (status);

CREATE TABLE IF NOT EXISTS change_request_attachment (
    id INTEGER NOT NULL,
    change_request_id INTEGER NOT NULL,
    filename VARCHAR(256) NOT NULL,
    content_type VARCHAR(128) NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    caption TEXT,
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_attachment_request_sha UNIQUE (change_request_id, sha256),
    FOREIGN KEY(change_request_id) REFERENCES change_request (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_attachment_change_request_id
    ON change_request_attachment (change_request_id);

CREATE TABLE IF NOT EXISTS edit_history (
    id INTEGER NOT NULL,
    target_type VARCHAR(11) NOT NULL,
    target_id INTEGER,
    change_request_id INTEGER,
    field VARCHAR(64) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    changed_by VARCHAR(64) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(change_request_id) REFERENCES change_request (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_edit_history_changed_at ON edit_history (changed_at);
CREATE INDEX IF NOT EXISTS ix_edit_history_target ON edit_history (target_type, target_id);
