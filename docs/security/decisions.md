# Security decisions log

> Per `docs/done/PLAN_editor_security_review.md`: each tier's decision points get a written choice + rationale here. Decisions are versioned by date; superseded entries stay for audit trail.
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

## D-T3.3 — File-upload retention

- **Date:** 2026-05-12
- **Decision:** **Deferred** — no upload endpoint exists; decision activates when the endpoint is wired.
- **Status:** Deferred (trigger-conditional)
- **Backing analysis:** [tier3-audit.md](tier3-audit.md) Phase 3.3.

### Choice

The decision menu (Indefinite / Time-bounded / Off-disk) cannot be made now because there's nothing to retain. The `change_request_attachment` schema is provisioned but no PHP endpoint accepts uploads. Re-open this decision when:

1. The Phase 1b file-upload wiring lands in `src/kayak/web/php/`.
2. AND the per-attachment use case is known (e.g., trip-report photos vs documentation PDFs — different lifetimes).

### Recommended posture when activated

If/when this decision becomes real, the default should be **Time-bounded** (delete attachments older than X months, with merged-proposal attachments getting longer retention). Rationale:
- Indefinite grows storage unboundedly and increases disclosure blast radius after backup leaks.
- Off-disk (S3-compatible) is overkill for the scale.
- Time-bounded balances cost vs forensic value.

But this is just a default; the per-use-case context at trigger time should drive the actual choice.

---

## D-T4.1 — Account deletion model

- **Date:** 2026-05-12
- **Decision:** **Option A** (Manual operator-handled via CLI).
- **Status:** Active (CLI to be implemented in Tier 6)
- **Backing analysis:** [tier4-audit.md](tier4-audit.md) Phase 4.1.

### Choice

When a user requests account deletion (via the documented "contact the club" path in `src/kayak/web/php/privacy.php`), the operator runs a new CLI tool — `levels delete-editor --email <addr>` — which cascades the deletes through `editor → editor_session / editor_magic_link / maintainer_credential / change_request → change_request_attachment` inside one transaction. `edit_history.changed_by` strings persist (audit-trail preservation); optional `--anonymize-history` flag rewrites `'editor:<id>' → 'deleted:<id>'`.

### Rationale

1. **Demand is theoretical.** Zero requests in the project's history; realistic volume < 1/year for a hobby/club site.
2. **Privacy policy already commits to the operator-handled path.** A CLI is the operational backbone of that promise.
3. **Self-serve UI for an unused feature is wasted effort.** Option C (hard-delete button) adds a destructive-action UI element whose primary failure mode (accidental click) outweighs its benefit at zero demand volume.
4. **Cascade behavior already supports manual deletion** — no schema change needed.

### Re-evaluation triggers

- Deletion-request volume hits ≥ 1/month (consider promoting to Option B/C).
- Regulator imposes self-serve right-to-be-forgotten obligation.
- Site scope grows beyond hobby/club.

---

## D-T4.2 — Data export

- **Date:** 2026-05-12
- **Decision:** **Option B** (On-request CLI export).
- **Status:** Active (CLI to be implemented in Tier 6)
- **Backing analysis:** [tier4-audit.md](tier4-audit.md) Phase 4.2.

### Choice

When a user requests an export of their data, the operator runs `levels export-editor --email <addr>` which dumps editor row + change_requests + edit_history (for that editor_id) to JSON. Pairs operationally with D-T4.1's manual deletion.

### Rationale

1. **Pairs with deletion.** Same workflow, same constraints; the operator can offer export-before-delete in one session.
2. **Trivial to implement** — a SELECT across three tables, JSON dump.
3. **Self-serve adds rate-limit / abuse surface** for a feature with no current demand.
4. **CLI is cheap insurance** vs. no-export — having the script ready means a same-day response to any request.

### Re-evaluation triggers

- First user export request → operator experience informs whether to elevate to self-serve.
- ≥ 1 export request/month sustained.
- GDPR-equivalent regulatory obligation.

---

## D-T4.3 — Retention of audit/PII tables

- **Date:** 2026-05-12
- **Decision:** Three-part decision (one per field family).
- **Status:** Active (purge CLI + systemd timer to be added in Tier 6)
- **Backing analysis:** [tier4-audit.md](tier4-audit.md) Phase 4.3.

### Choice

| Sub-decision | Choice |
|---|---|
| 4.3a `edit_history.changed_by` | **A — Indefinite.** PII linkage is broken at editor-deletion time (D-T4.1), not via audit-trail decay. |
| 4.3b `editor_magic_link.ip_issued` (plus the whole row) | **B — Purge** rows where `expires_at < now - 90 days`. |
| 4.3c `editor_session.ip` + `user_agent` (plus the whole row) | **C — Delete** rows where `expires_at < now - 90 days`. No FK to `editor_session.id` exists; clean delete. |

Implementation: new `levels editor-retention` CLI + daily `kayak-editor-retention.timer` systemd unit.

### Rationale

1. **`edit_history` is content audit, not PII log.** The string `editor:42` resolves to PII only via the live `editor` table; severing PII at editor-deletion time is the right hook.
2. **90 days = conventional incident-discovery window** for credential issuance logs. Operator notices something off → ~3 months of issuance history to correlate.
3. **Magic-link and session rows past 90-day post-expiry are dead weight.** The token_hash is stale, the IP is dead PII, and the operational value is zero.
4. **`editor_session` rows can be hard-deleted** (verified no FK references `editor_session.id`); no need for nullify-only.

### Re-evaluation triggers

- An incident requires retroactive credential-issuance forensics beyond 90 days → consider 180 or 365 days.
- Storage scale changes (each row is currently small; not a current concern).
- Compliance regime requires explicit retention SLA.

---

## D-T4.4 — Privacy policy + Terms of Service

- **Date:** 2026-05-12
- **Decision:** **Option B** (Refresh privacy.php to fix F-16; defer ToS).
- **Status:** Active (refresh to be implemented in Tier 6)
- **Backing analysis:** [tier4-audit.md](tier4-audit.md) Phase 4.4. New finding [F-16](findings.md#f-16) filed.

### Choice

1. Refresh `src/kayak/web/php/privacy.php` to fix F-16 (the "Your Rights" section contradicts the upper "Data We Collect" section — likely written before the editor pipeline existed).
2. Update "Last updated" date; add annual-review HTML comment trigger.
3. Do NOT add a Terms of Service page at this time.
4. Do NOT pursue lawyer review.

### Rationale

1. **F-16 must be fixed regardless** — a self-contradicting privacy policy is worse than a brief one.
2. **ToS is overkill at hobby-club scale.** No revenue, no commercial liability surface, no DMCA volume. Implicit terms (be reasonable, respect copyright, contributions licensed back to WKCC) are conventional and don't need formalization.
3. **Lawyer review** is appropriate only when revenue or specific compliance regimes (GDPR / COPPA / CCPA scope) apply — none do today.
4. **Annual review trigger** anchors the page to a calendar, paired with Tier 5's re-review cadence.

### Re-evaluation triggers

- New data class added (e.g., file-upload activates D-T3.3 → privacy needs an attachment paragraph).
- Revenue stream introduced.
- Geographic expansion brings new compliance regimes into scope.
- Annual calendar trigger.

---

## D-T4.5 — `security.txt` content

- **Date:** 2026-05-12
- **Decision:** **Option A + D** (Keep the production RFC 9116 minimum; add Expires refresh to annual maintenance calendar).
- **Status:** Active (WKCC production values now live in the dataset `site.yaml`; calendar item for 2027-04-01)
- **Backing analysis:** [tier4-audit.md](tier4-audit.md) Phase 4.5.

### Choice

WKCC production content is dataset-owned in `kayak_data/site.yaml`:
```
Contact: mailto:pat.kayak@gmail.com
Expires: 2027-05-20T00:00:00Z
Preferred-Languages: en
```

The engine fallback at `src/kayak/web/static/security.txt` is generic scaffold
content; deployment renders the dataset values when present.

Calendar reminder for 2027-04-01: refresh the dataset `security_expires` value
to 2028-05-20 (or +1 year from refresh date).

Defer PGP `Encryption:` line and `Acknowledgments:` / `Policy:` lines until concrete triggers.

### Rationale

1. **Current minimum is RFC 9116 compliant.** Adds Preferred-Languages as a courtesy.
2. **PGP adds friction without uptake.** Researcher base at hobby-site tier reaches via plain email; PGP key custody is non-trivial ongoing cost.
3. **Acknowledgments needs content.** An empty page is worse than no link; defer to first concrete disclosure.
4. **Policy URL waits on Tier 5.** Until IR cadence is decided, no Policy page exists to link.

### Re-evaluation triggers

- Security researcher requests PGP.
- Security researcher requests Acknowledgments.
- Tier 5 decides IR cadence + creates a Policy URL → add `Policy:` line.
- Annual Expires refresh (2027-04-01).

---

## D-T5.1 — Vulnerability disclosure path

- **Date:** 2026-05-12
- **Decision:** **Option A** (`security.txt` only; GHSA activation deferred to first-report trigger).
- **Status:** Active
- **Backing analysis:** [tier5-audit.md](tier5-audit.md) Phase 5.1.

### Choice

Keep the current production `security.txt` minimum (dataset-owned `Contact` +
`Expires`, plus `Preferred-Languages` from the engine template — fixed by
D-T4.5). No GitHub Security Advisories (GHSA), no HackerOne, no bug bounty.

### Rationale

1. **Zero historical reports** — the channel isn't a bottleneck.
2. **Best-effort response commitment (D-T5.2)** is honest given single-operator availability; promising a faster coordinated channel without resourcing it would be dishonest.
3. **GHSA is the natural promotion path** when the first coordinated report arrives. One click in the GitHub repo enables Private Vulnerability Reporting; security.txt gains a `Policy:` line then. Zero pre-investment.
4. **HackerOne / bounty** mismatched to scale.

### Re-evaluation triggers

- First coordinated-disclosure report → activate GHSA, add `Policy:` to security.txt.
- Researcher requests PGP / coordinated-publish.
- Disclosure volume ≥ 1/quarter.

---

## D-T5.2 — Incident-response cadence

- **Date:** 2026-05-12
- **Decision:** **Option A** (Best-effort, documented in [incident-response.md](incident-response.md)).
- **Status:** Active
- **Backing analysis:** [tier5-audit.md](tier5-audit.md) Phase 5.2.

### Choice

Best-effort response. Concrete commitments documented in `incident-response.md`:
- Security gmail checked at least once per business day.
- Initial acknowledgment within 2 business days.
- Triage decision within 5 business days for non-urgent reports; same-day for active-abuse reports.

### Rationale

1. **Aspirational SLAs that aren't enforced are noise** — they create false reporter expectations without changing actual response time.
2. **Single-operator availability is constrained by vacation / illness / day-job.** Routinely-missed SLAs erode trust faster than honest "best-effort" framing.
3. **Best-effort, documented in concrete language** in the runbook, is the truthful posture.

### Re-evaluation triggers

- Second maintainer joins → 24h triage SLA becomes credible.
- Site scope grows beyond hobby/club.
- Compliance regime requires documented SLA.

---

## D-T5.3 — Re-review cadence

- **Date:** 2026-05-12
- **Decision:** **Option B + light annual touch** (major-change-triggered focused re-review + ~half-day annual housekeeping).
- **Status:** Active
- **Backing analysis:** [tier5-audit.md](tier5-audit.md) Phase 5.3.

### Choice

**Major-change triggers** (focused re-review of the affected slice):
- New PHP endpoint joining the editor pipeline.
- New DB table containing PII or holding credentials.
- New external service (auth, hosting move, CDN, captcha replacement).
- New privileged operation (file upload, bulk delete, etc.).
- Major dependency upgrade (PHP-FPM major version, nginx replaced, SQLite → other DB).

**Annual light touch** (every 12 months from Tier 5 closeout, so next: ~2027-05-12):
- Re-read findings.md + decisions.md.
- Check whether re-evaluation triggers fired since last review.
- Refresh the WKCC dataset `security_expires` value (per D-T4.5).
- Update README Tier status table.
- Effort: ~half-day.

### Rationale

1. **Once-and-done discards the audit infrastructure.** findings.md + decisions.md are designed to be re-checked.
2. **Annual full re-review** is over-investment at hobby scale; major changes happen rarely.
3. **Major-change-only alone misses drift detection.** Code and dependencies shift even without major changes. The annual light touch is the minimal counter-drift mechanism.

### Re-evaluation triggers

- A "major change" trigger fires.
- An incident occurs (post-mortem includes re-evaluating decisions touched by the incident).
- Second maintainer joins (fresh eyes are themselves a re-review event).

---

## (placeholder) D-T5.x — Vulnerability disclosure / IR cadence / re-review

_Filled above (D-T5.1, D-T5.2, D-T5.3)._

## Decision summary

| Id | Topic | Decision | Date | Status |
|---|---|---|---|---|
| D-T1.3 | Maintainer 2FA model | Option A (magic-link only) with documented re-eval triggers | 2026-05-12 | Active |
| D-T2.4 | Audit trail tamper resistance | Option A (None) — rely on backups + web-side controls; re-eval triggers documented | 2026-05-12 | Active |
| D-T3.3 | File-upload retention | Deferred — N/A (no upload endpoint); default would be Time-bounded if/when activated | 2026-05-12 | Deferred |
| D-T4.1 | Account deletion | Option A (manual operator-handled `levels delete-editor` CLI) | 2026-05-12 | Active (CLI shipped Tier 6) |
| D-T4.2 | Data export | Option B (on-request `levels export-editor` CLI) | 2026-05-12 | Active (CLI shipped Tier 6) |
| D-T4.3 | Retention (audit / IPs / UAs) | 3-part: history indefinite; magic-link 90d purge; session 90d delete | 2026-05-12 | Active (CLI + timer shipped Tier 6) |
| D-T4.4 | Privacy + ToS | Option B (refresh privacy.php — fix F-16; defer ToS) | 2026-05-12 | Active (impl Tier 6) |
| D-T4.5 | `security.txt` | Option A + D (keep current minimum; annual Expires refresh) | 2026-05-12 | Active |
| D-T5.1 | Vulnerability disclosure path | Option A (security.txt only; GHSA on first-report trigger) | 2026-05-12 | Active |
| D-T5.2 | IR cadence | Option A (Best-effort, documented in incident-response.md) | 2026-05-12 | Active |
| D-T5.3 | Re-review cadence | Option B + light annual touch (major-change-triggered + ~half-day yearly) | 2026-05-12 | Active |
