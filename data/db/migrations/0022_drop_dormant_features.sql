-- Migration 0022: drop the maintainer_credential table per T3.5
-- (PLAN_pre_release_followup.md / PLAN_outstanding_followups.md Phase 4.2).
--
-- Per audit ARCH-H10, several schema features were on the books with
-- zero consumer code. Of the four candidates the audit flagged for
-- removal, this migration only addresses the genuinely-unused table:
--
-- DROPPED:
--   maintainer_credential (+ ix_maintainer_credential_editor_id)
--     — WebAuthn passkey schema; never wired to register/assert code.
--     Live DB has zero rows (verified 2026-05-14). The
--     editor --CASCADE--> maintainer_credential FK is harmless to
--     drop because there's nothing to cascade.
--
-- DELIBERATELY NOT INCLUDED (audit was wrong / unjustified):
--   * ChangeStatus.auto_applied / ChangeTarget.trip_report — both
--     are pure code (SQLite stores both as TEXT, no CHECK). Removing
--     them from kayak.db.models shrinks the SQLAlchemy-derived
--     VARCHAR length, which trips the schema-parity test against the
--     existing live DB (target_type VARCHAR(11) vs ORM-emit VARCHAR(6)).
--     Keeping the enum values lets the parity test stay strict
--     without a table-rebuild migration.
--   * EditorStatus.minimal — the audit claimed minimal "never
--     authorizes anything" but that's wrong. admin.php promotes
--     pending->minimal as the first review step; propose_handler
--     has a minimal-specific daily cap (10/day); the live DB has 1
--     editor with status='minimal'. Kept.

DROP INDEX IF EXISTS ix_maintainer_credential_editor_id;
DROP TABLE IF EXISTS maintainer_credential;
