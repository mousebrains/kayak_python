# Security decisions log

> Per `docs/PLAN_editor_security_review.md`: each tier's decision points get a written choice + rationale here. Decisions are versioned by date; superseded entries stay for audit trail.
>
> Cross-references: [findings.md](findings.md) for the gaps being decided about; [tier1-audit.md](tier1-audit.md) (and future per-tier audit logs) for the analysis backing each decision.

## D-T1.3 — Maintainer 2FA model

- **Date:** 2026-05-12
- **Decision:** **Option A** (magic-link only) with explicit re-evaluation triggers.
- **Status:** Active
- **Backing analysis:** [tier1-audit.md](tier1-audit.md) Phase 1.3.

### Choice

Stay with magic-link-only authentication for maintainer accounts. Do not advance Phase 1b WebAuthn wiring at this time.

### Rationale

1. **Seed-maintainer CLI is an unusually strong control.** No web path elevates an editor to `status='maintainer'`. Even if a maintainer account is compromised, the attacker cannot create another maintainer. Confirmed in Phase 1.3 audit (a).
2. **Single-maintainer posture today.** Implementing WebAuthn for the one operator is high-effort/low-leverage. The realistic threat reduces to "maintainer's Gmail compromised" — a vector the maintainer can independently strengthen via Gmail's own 2FA (presumed in place; reaffirm during Tier 5 incident-response runbook drafting).
3. **F-4 is a higher-leverage security spend.** A 1-2 day budget for editor-pipeline security work is better spent on `edit_history` tamper-resistance (an external sink or append-only journal) — strong audit + weak auth is an inconsistent posture; the audit side is the weaker control today.
4. **Future-maintainer UX concern.** Onboarding non-tech-savvy contributors as maintainers (via the seed CLI) is plausible. WebAuthn's UX hard-floors at "comfortable with platform passkeys / Face ID / Windows Hello"; that's not universal. Deferring lets us pick per-maintainer when there's a concrete person.

### Re-evaluation triggers

Revisit this decision when ANY of the following happens:

- **A second maintainer is added** via `levels seed-maintainer`. Evaluate that person's skill level and decide whether to require Phase 1b WebAuthn for them (or accept A per-maintainer with documented risk).
- **F-4 (`edit_history` tamper-resistance) is implemented.** Strong audit + weak auth is inconsistent; consider advancing B then.
- **A new privileged operation is added** (e.g., DB-level bulk delete from the web layer, file upload, anything that increases maintainer-compromise blast radius).
- **An incident occurs** involving a maintainer-account compromise or a near-miss.

### Open precondition

- Maintainer's Gmail account 2FA is presumed to be enabled (TOTP or hardware key). Tier 5 incident-response runbook should include this as a documented dependency.

---

## D-T2.4 — Audit trail tamper resistance

- **Date:** 2026-05-12
- **Decision:** **Option A** (None) with explicit re-evaluation triggers.
- **Status:** Active
- **Backing analysis:** [tier2-audit.md](tier2-audit.md) Phase 2.4.

### Choice

Do not add in-DB tamper-resistance to `edit_history`. Rely on:
- Existing Hetzner storage-box backup + rclone offsite (per `docs/offsite-backup.md`) for *daily-granularity* external snapshots that enable post-incident forensics via diff.
- Web-side controls (maintainer auth, no SQL injection) for active-attack prevention (already verified in Phases 2.1-2.3).
- Per-row `changed_by` + `changed_at` for attribution within an honest scenario.

### Rationale

1. **Realistic threat is post-incident forensics**, not active prevention. The web layer cannot delete `edit_history` rows (no DELETE endpoint exists; verified via grep). Only shell-level breach of the prod host enables tampering, and at that point ALL system data is at risk — `edit_history` is not the long pole.
2. **Backups already provide partial integrity.** A daily snapshot stored offsite is sufficient to detect post-hoc tampering for the hobby/club threat model. Not a cryptographic chain, but adequate.
3. **Single-operator + no compliance regime.** No external mandate requires cryptographic audit; no audit framework reads `edit_history`.
4. **Option B (append-only journal) doesn't defend against shell-level breach** — and shell-level breach is the realistic threat vector. Doesn't pass cost-benefit.
5. **Option C (external sink) is real protection but introduces a hard external dependency** on every write path. Adds operational complexity disproportionate to current threat model.

### Re-evaluation triggers

Revisit when ANY of the following happens:

- **Incident occurs** with suspected `edit_history` tampering, or maintainer-account compromise that gained DB write access.
- **Second maintainer joins.** Same trigger as D-T1.3 / F-5 / F-13. Multi-maintainer setup increases insider-threat surface.
- **Compliance / audit requirement appears.** Unlikely for hobby/club site.
- **Scope grows beyond hobby/club tier.** E.g., commercial liability for misleading reach data; regulator interest in trip-report fidelity.

---

## (placeholder) D-T3.x — File-upload retention

To be filled when Tier 3 lands AND the upload endpoint is wired. Decision menu:
- Indefinite
- Time-bounded (delete attachments older than X months)
- Off-disk (S3-compatible bucket, signed URLs)

## (placeholder) D-T4.x — Account lifecycle / data export / retention / privacy / security.txt

To be filled when Tier 4 lands. Multiple decision points; see plan.

## (placeholder) D-T5.x — Vulnerability disclosure / IR cadence / re-review

To be filled when Tier 5 lands.

## Decision summary

| Id | Topic | Decision | Date | Status |
|---|---|---|---|---|
| D-T1.3 | Maintainer 2FA model | Option A (magic-link only) with documented re-eval triggers | 2026-05-12 | Active |
| D-T2.4 | Audit trail tamper resistance | Option A (None) — rely on backups + web-side controls; re-eval triggers documented | 2026-05-12 | Active |
