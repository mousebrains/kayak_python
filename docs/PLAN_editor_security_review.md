# Plan — Editor feature security review

> **Cross-check:** plan drafted 2026-05-11 against the editor surface deployed in `project_editor_feature` (Phase 1+2). The structure of this plan is **menu-style** for tiers 2–5 because the user has flagged that real-world options matter — each tier presents the choice space rather than a prescriptive pick. Tier 0 (threat model) is the only tier whose output is mandatory before subsequent tiers can be sized.
>
> A second Claude session should re-run the read-only commands in **§Reproduce** to confirm the editor-related file and DB-table inventory hasn't shifted.

## Why

The editor / propose / review / contact pipeline went live as Phase 1+2 of `project_editor_feature`. Real user accounts exist (`editor`, `editor_session`, `editor_magic_link`, `maintainer_credential`). Real attachments are accepted (`change_request_attachment`). Real audit history is recorded (`edit_history`). The PHP surface area (`auth.php`, `login.php`, `logout.php`, `edit.php`, `propose.php`, `review.php`, `comment.php`, `account.php`, `admin.php`) was built incrementally by a single operator without a security pass.

Two motivating risks:
1. **Hobby-grade controls now serve real users.** A single XSS, broken session check, missing CSRF token, or IDOR can leak credentials, deface content, expose private edit history, or escalate an editor account to maintainer. Today no one is watching for any of these — the production-discipline plan addresses outage detection, not security incidents.
2. **Single-operator, no 24/7 response capability.** A real exploit at 3am sits in production until someone notices. The realistic posture is "controls must work without supervision" — preventive over detective.

Goal of this plan: produce a documented security posture (what is in place, what isn't, what's been deferred and why) plus a prioritized findings list. The plan is deliberately option-rich rather than prescriptive — each tier 2–5 ends with a **decision point** so you can pick the option that matches what you're realistically willing to operate.

## Constraints

- **Single operator.** No 24/7 incident response. No security team. Triage and patches happen during your awake hours.
- **No SLA, no compliance regime.** Not GDPR-required, not CCPA-required, not HIPAA. *Choosing* to honor parts of those frameworks is a decision point, not an obligation.
- **Live PHP-FPM lacks mbstring** ([reference_php_no_mbstring]).
- **CSP + Turnstile already in place** for `/contact.php`. Existing `tests/php/AuthTest.php`, `EditAuthTest.php`, `TurnstileTest.php`, `ReviewApproveRaceTest.php` cover slices of auth and propose paths.
- **Hetzner backups + rclone offsite** ([reference_hetzner_backups], [reference_offsite_backup]) cover data loss but not unauthorized data access.
- **The PHP layer split** (`PLAN_php_layer_split.md`) may be in flight or complete; security findings in `auth.php` should drive its split timing in Tier 5 of the PHP plan, not the other way around.
- **Phased.** Tier-by-tier review like the prior plans.

## Decisions deferred to per-tier menus

The following choices need real-world calibration and are NOT prescribed up front:

- Account lifecycle: full GDPR-style export+deletion vs deactivation-only vs nothing
- Vulnerability disclosure: HackerOne / bug bounty / `security.txt` only / no public path
- Incident response cadence: best-effort / 24h target / 4h target
- Re-review cadence: annual / quarterly / on-major-change-only
- Whether to publish a written privacy policy + terms of service
- Whether maintainer-tier accounts get hardware 2FA, app TOTP, or magic-link only
- Log retention duration for `edit_history` and security events
- Whether to offer self-serve account deletion vs manual operator-handled

Each decision shows up at the end of the relevant tier with options + tradeoffs. You make the pick; the next phase assumes that pick.

## Migration tiers

Tier 0 is mandatory + not menu-driven (it produces the inventory the rest of the plan needs). Tiers 1–5 each end with a decision point. Tier 6 closes out with whatever was decided.

### Tier 0 — Threat model + inventory (mandatory)

**Goal:** Produce two artifacts that the rest of the plan reads from.

1. **Phase 0.1 — Surface inventory.** `docs/security/editor-surface.md`:
   - Every PHP entry point that touches editor data (file path + URL)
   - Every DB table with PII or auth material (table name + sensitive columns)
   - Every external service the editor talks to (mailer, Turnstile, anything else)
   - Every cookie set by the editor and its attributes
   - File-upload paths (where attachments land on disk; access permissions)
2. **Phase 0.2 — Data classification.** Tag every column from Phase 0.1 with one of: **public** / **internal** / **PII** / **credential**. PII includes email; credential includes session tokens, magic-link tokens, and any password material.
3. **Phase 0.3 — Threat model.** `docs/security/threat-model.md`. STRIDE-style is fine (Spoofing / Tampering / Repudiation / Information disclosure / Denial of service / Elevation of privilege). For each: who's the realistic threat actor (curious user / disgruntled editor / opportunistic attacker / targeted attacker), and what's the worst-case impact. Skip "nation-state" — proportional threat model only.
4. **Phase 0.4 — Existing-controls map.** For each threat from 0.3, list what's already in place (e.g. "CSRF on edit.php: ✓ via hidden token, see auth.php:142"). Gaps go on the findings list.

**Verification gate (end of Tier 0):**
- Three documents exist (`editor-surface.md`, `threat-model.md`, controls map)
- Findings list has at least 5 entries (if it doesn't, you missed things — go look harder)
- Reviewed with one other set of eyes if practical

### Tier 1 — Authentication review

**Goal:** Confirm magic-link login, session management, and cookie attributes do what they're supposed to. No menu — these are mostly objective checks.

1. **Phase 1.1 — Magic-link audit.** Token generation entropy (CSPRNG?), token length, single-use enforcement, expiry window, replay prevention, link-leakage in mail headers / logs / Referer. Test: request a magic link; capture the token; use it; try to use it again; expect failure.
2. **Phase 1.2 — Session audit.** Cookie attributes (`Secure`, `HttpOnly`, `SameSite=Lax` or `Strict`), session-id rotation on login, session-fixation protection, logout invalidation (server-side, not just cookie clear), idle/absolute timeout. Test: login; capture cookie; logout; replay cookie; expect 401.
3. **Phase 1.3 — Maintainer credential audit.** What's `maintainer_credential` storing? If passwords: are they argon2id / bcrypt / scrypt with appropriate parameters? Salt? Pepper? If API keys: rotation procedure documented? Revocation tested?
4. **Phase 1.4 — Brute-force / credential-stuffing posture.** Per-IP and per-account rate limits on login. Lockout policy. Turnstile on what endpoints. Per-IP magic-link request rate limit.
5. **Phase 1.5 — Account-recovery flow.** Forgotten-magic-link handling. Email-changed handling. Account-takeover via email compromise: what's the blast radius?

**Verification gate (end of Tier 1):**
- Each of the above tested with a written log of pass/fail/N/A
- Failures filed as findings
- Mitigation effort estimated for each finding

**Decision point — maintainer 2FA model:**
- **Magic-link only** (current). Cheapest, no UX friction, but compromised email = compromised account.
- **TOTP via app** (Authy/Google Authenticator). Standard. Some user-data plumbing needed; QR setup flow.
- **WebAuthn / hardware key**. Strongest. Requires a separate hardware purchase per maintainer; UX overhead.

### Tier 2 — Authorization review

**Goal:** Confirm editors can't see or modify what they shouldn't; maintainers can't be impersonated by editors.

1. **Phase 2.1 — Role enforcement audit.** Every endpoint that requires editor-or-maintainer status: is the check present? Is it consistent (helper function, not ad-hoc)? Test: hit each endpoint without auth, with editor cookie, with maintainer cookie; expect 401/200/200 respectively.
2. **Phase 2.2 — IDOR sweep.** Every endpoint that takes an ID parameter (reach_id, change_request_id, attachment_id, etc.): does it verify the requester is allowed to access that specific row? Test: request your own object; record the URL; replay it from a different account; expect 403.
3. **Phase 2.3 — Privilege escalation paths.** Can an editor promote themselves to maintainer through any code path? Mass-assignment in update endpoints? File upload that writes to a privileged location? SQL injection that lets you insert into `maintainer_credential`?
4. **Phase 2.4 — Audit trail integrity.** Can `edit_history` be modified post-hoc? Can entries be deleted? Is there a hash chain or external log?

**Verification gate (end of Tier 2):**
- Authorization matrix documented (role × endpoint → expected response)
- Each row tested
- IDOR sweep produced no findings (or each one is filed)

**Decision point — audit-trail tamper resistance:**
- **None** (current). DB-level access trusts the operator.
- **Append-only journal** (writes to `~/logs/edit_audit.log`, no delete path). Simple, partial protection.
- **External sink** (e.g. ship to a cheap S3-compatible bucket in append-only mode). Real protection; ~$1/month.

### Tier 3 — Input/output handling

**Goal:** Confirm XSS, SQLi, file-upload, and rate-limit controls hold.

1. **Phase 3.1 — XSS sweep.** Every place user-supplied content reaches HTML output: is it escaped? `htmlspecialchars` with `ENT_QUOTES`? Or templated through something safer? Test: submit `<script>alert(1)</script>` everywhere accepting input; visit pages that render it; expect no execution.
2. **Phase 3.2 — SQLi sweep.** Every PDO call: parameterized? No string concatenation? PHPStan should flag the obvious cases. Manual review for the rest.
3. **Phase 3.3 — File-upload audit.** What types accepted? Size limit? MIME validation? Filename sanitization (path traversal)? Upload destination — is it served by nginx? If so, with what `Content-Type` headers? Stored with execute permissions stripped?
4. **Phase 3.4 — Rate limiting + abuse posture.** What's behind Turnstile (currently `/contact.php` per [project_editor_feature]). Should `propose.php`, `comment.php`, magic-link request be too? Rate limits at nginx layer? Application layer? Account-level vs IP-level?
5. **Phase 3.5 — CSRF audit.** State-changing endpoints: token check? Token bound to session? Compared time-constant?

**Verification gate (end of Tier 3):**
- Each input vector tested (or marked N/A with reason)
- File-upload posture documented and tested
- CSRF coverage matrix complete

**Decision point — file-upload retention:**
- **Indefinite** (current, presumably). Simplest; storage grows.
- **Time-bounded** (delete attachments older than X months; attached to merged change requests last longer). Reduces blast radius of disclosure; needs a janitor job.
- **Off-disk** (S3-compatible bucket, signed URLs). More moving parts; smaller on-prod-disk attack surface.

### Tier 4 — User-data obligations

**Goal:** Decide and document what's offered for account deletion, data export, retention, and user-facing policy.

This tier is nearly all decision points — there's no "right answer" without external regulation. Each item presents the menu.

1. **Decision point — account deletion:**
   - **Manual operator-handled.** User emails you; you run a script. Documented in `docs/operations.md`. Cheap; slow.
   - **Self-serve deactivation.** Account is locked, login disabled, but data retained. Easier to undo; doesn't satisfy "delete my data."
   - **Self-serve hard delete.** UI button → cascading delete of `editor`, `editor_session`, `editor_magic_link`, `change_request*`, `edit_history` (or anonymization). Real "right to be forgotten." Most work.
2. **Decision point — data export:**
   - **No export.** User has no way to retrieve their own data. Not a problem unless someone demands it.
   - **On-request export.** User emails you; you produce JSON/CSV. Manual.
   - **Self-serve export.** UI button → JSON download. Most work.
3. **Decision point — `edit_history` retention:**
   - **Indefinite** (current). Useful for audit; arguably-PII grows.
   - **N years then anonymize the actor.** Edit content stays, "who" is dropped after some retention.
4. **Decision point — privacy policy + terms:**
   - **None** (current). Defensible for a hobby site; weak if someone formally demands.
   - **`/privacy.html` + `/terms.html`** boilerplate. ~1 hour to draft; sets expectations; minimal commitment.
   - **Lawyer-reviewed**. Days of work + cost; appropriate if revenue or specific obligations.
5. **Decision point — `security.txt`:**
   - **Skip.** No published disclosure path.
   - **Email-only.** `Contact: mailto:security@mousebrains.com` (or alias). Cheapest; sets expectations; you're committing to read that mailbox.
   - **Email + PGP.** Add a public key + fingerprint. Marginally more secure for the discloser; modestly more work to set up.

**Verification gate (end of Tier 4):**
- Each decision point has a written choice + rationale in `docs/security/decisions.md`
- Anything chosen is implemented (Tier 6) or explicitly deferred with a date

### Tier 5 — Disclosure + response

**Goal:** Decide how vulnerabilities reach you, what you commit to doing about them, and how often the whole posture is re-checked.

1. **Decision point — vulnerability disclosure path:**
   - **`security.txt` only** (per Tier 4 decision). Anyone who finds a bug emails you.
   - **GitHub Security Advisories.** Coordinated disclosure via GitHub. Free; assumes the codebase is open-source-ish.
   - **HackerOne / Bugcrowd platform.** Formal reporting platform. Free tier exists; mostly overkill for a hobby site.
   - **Bug bounty.** $X per finding. Unnecessary at this scale; tempting only if you want serious researcher attention.
2. **Decision point — incident response cadence:**
   - **Best-effort.** No commitment. Honest for a hobby project.
   - **24h triage / 7d patch target** for high-severity. Aspirational but unenforced.
   - **Real SLA.** 4h triage / 48h patch. Requires you to actually be reachable; pairs poorly with single-operator.
3. **Decision point — re-review cadence:**
   - **Once and done.** Run this plan, fix findings, walk away.
   - **On-major-change-only.** New auth flow / new privileged endpoint / new data type triggers a focused re-review.
   - **Annual.** Calendar entry; ~one week of work each year.
4. **Phase 5.4 — Incident-response runbook.** `docs/security/incident-response.md`: discovered-during-business-hours flow vs out-of-hours; user notification template; Hetzner abuse contact; how to revoke all sessions; how to roll a leaked secret. Even at "best-effort" you need this written down.
5. **Phase 5.5 — Backup-restore drill from a security angle.** The Tier 4.4 production-discipline drill restores from backup; this version assumes the live DB is *poisoned* (attacker-modified rows). Restore to a known-good earlier snapshot; identify what's lost; document.

**Verification gate (end of Tier 5):**
- All Tier 5 decisions documented in `docs/security/decisions.md`
- Incident-response runbook exists and is concretely actionable
- Drill log exists for the restore-from-poisoned-state scenario

### Tier 6 — Hardening + closeout

**Goal:** Apply Tier 1–3 findings; implement Tier 4–5 decisions.

1. **Phase 6.1 — Apply findings.** Each Tier 1–3 finding is fixed, deferred-with-date, or accepted-as-risk-with-rationale. The findings list becomes a punch list.
2. **Phase 6.2 — Implement Tier 4 decisions.** Account deletion / export / retention / policy / `security.txt` per the picks made.
3. **Phase 6.3 — Implement Tier 5 decisions.** Disclosure path / IR runbook / re-review schedule.
4. **Phase 6.4 — Final pass.** Everything in `docs/security/` is up to date. Posture document `docs/security/posture.md` summarizes what's in place, what's deferred, what was explicitly accepted as risk.

**Verification gate (end of Tier 6):**
- Punch list is empty (every finding has a status)
- `docs/security/posture.md` exists and reflects reality
- A non-Pat reader can answer "what's the plan if a 0day drops in PHP?" from the runbook

## Risks (of doing this work)

- **Cataloguing security gaps without fixing them = audit risk.** If you write down "XSS in `propose.php`" and don't patch it for six months, the document becomes evidence-of-knowledge in any post-incident review. Mitigation: fix-as-you-find at least for high-severity, even if it breaks the tier ordering.
- **`security.txt` commits you to a process.** Publishing one and then ignoring reports is worse than not publishing. Only publish what you'll honor.
- **Privacy policy similarly commits you.** Don't draft "we delete on request within 30 days" if you can't deliver.
- **Tier 0 inventory work is invisible.** Easy to skip; everything downstream is wrong without it.
- **Single-operator burnout.** This plan adds operational obligations that don't go away. Be honest at each decision point about what you'll actually maintain.

## Out of scope

- **External penetration test.** Worth doing eventually; out of scope here. A focused tester for ~2–4 days runs ~$3-10K.
- **Web application firewall** (Cloudflare WAF, etc.). Separate decision; orthogonal to this plan.
- **Code-signing / supply-chain hardening.** Composer + uv lockfiles cover the basics; deeper SBOM work is a separate plan.
- **Compliance certifications** (SOC 2, ISO 27001). Not relevant for a hobby/club site.
- **DDoS protection** beyond what Hetzner provides. Separate plan.
- **PHP-layer code refactor** for security. Tracked in `PLAN_php_layer_split.md`. If a security finding lands in `auth.php` while the PHP split is in flight, fix the security finding first.

## Reproduce

Read-only commands a second session should run before Tier 0 starts.

```bash
# Editor-surface PHP files
ls -la php/account.php php/admin.php php/auth.php php/comment.php \
       php/edit.php php/login.php php/logout.php php/propose.php \
       php/review.php php/contact.php 2>/dev/null

# Editor-related includes
grep -lE "editor_session|magic_link|maintainer_credential|change_request" php/includes/

# DB schema for editor tables
grep -A 8 "class Editor\|class EditorSession\|class EditorMagicLink\|class MaintainerCredential\|class ChangeRequest\|class EditHistory" src/kayak/db/models.py

# Existing security tests
ls tests/php/

# CSP / nginx surface for editor endpoints (per project_editor_feature memory)
sudo nginx -T 2>/dev/null | grep -B2 -A8 "/contact.php\|/edit.php\|/propose.php\|/review.php\|Turnstile\|Content-Security-Policy"

# Turnstile and Turnstile integration locations
grep -rn "turnstile\|Turnstile" php/ src/ 2>/dev/null

# File-upload paths (Tier 3.3)
grep -rn "move_uploaded_file\|\\\$_FILES" php/ 2>/dev/null

# Existing security docs (likely none)
ls docs/security/ 2>/dev/null
```
