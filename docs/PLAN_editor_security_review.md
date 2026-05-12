# Plan — Editor feature security review

> **Cross-check:** plan drafted 2026-05-11 against the editor surface deployed in `project_editor_feature` (Phase 1+2). The structure of this plan is **menu-style** for tiers 2–5 because the user has flagged that real-world options matter — each tier presents the choice space rather than a prescriptive pick. Tier 0 (threat model) is the only tier whose output is mandatory before subsequent tiers can be sized.
>
> A second Claude session should re-run the read-only commands in **§Reproduce** to confirm the editor-related file and DB-table inventory hasn't shifted.

## Why

The editor / propose / review / contact pipeline went live as Phase 1+2 of `project_editor_feature`. Real user accounts exist (`editor`, `editor_session`, `editor_magic_link`). Real audit history is recorded (`edit_history`). The PHP surface area (`auth.php`, `login.php`, `logout.php`, `edit.php`, `propose.php`, `review.php`, `comment.php`, `account.php`, `admin.php`) was built incrementally by a single operator without a security pass.

**Provisioned-but-not-wired:** the `change_request_attachment` table exists (file uploads with `sha256` content-addressed storage path); the `maintainer_credential` table exists for WebAuthn passkeys. Neither feature has a live PHP endpoint as of plan-draft date. The plan flags both as "schema present, behavior pending" — Tier 3.3 file-upload audit is currently N/A; Tier 1.3 maintainer-credential audit asks "advance Phase 1b wiring or stay at magic-link-only?" rather than auditing a present-but-broken implementation.

Two motivating risks:
1. **Hobby-grade controls now serve real users.** A single XSS, broken session check, missing CSRF token, or IDOR can leak credentials, deface content, expose private edit history, or escalate an editor account to maintainer. Today no one is watching for any of these — the production-discipline plan addresses outage detection, not security incidents.
2. **Single-operator, no 24/7 response capability.** A real exploit at 3am sits in production until someone notices. The realistic posture is "controls must work without supervision" — preventive over detective.

Goal of this plan: produce a documented security posture (what is in place, what isn't, what's been deferred and why) plus a prioritized findings list. The plan is deliberately option-rich rather than prescriptive — each tier 2–5 ends with a **decision point** so you can pick the option that matches what you're realistically willing to operate.

## Constraints

- **Single operator.** No 24/7 incident response. No security team. Triage and patches happen during your awake hours.
- **No SLA, no compliance regime.** Not GDPR-required, not CCPA-required, not HIPAA. *Choosing* to honor parts of those frameworks is a decision point, not an obligation.
- **Live PHP-FPM lacks mbstring** ([reference_php_no_mbstring]).
- **CSP + Turnstile** in place for `/login.php` and `/contact.php` via `/etc/nginx/snippets/security-headers-turnstile.conf`. Default snippet `/etc/nginx/snippets/security-headers.conf` covers other PHP locations. (Snippets live on the prod host only — not in `deploy/`.)
- **Existing test coverage:** `tests/php/AuthTest.php` (magic-link rate limit), `tests/php/EditAuthTest.php` (edit-flow auth), `tests/php/TurnstileTest.php` (Turnstile env-var precedence), `tests/php/ReviewApproveRaceTest.php` (review concurrency), `tests/php/SanityTest.php` (URL-validation primitives in auth.php), `tests/php/DbFallbackTest.php` (db.php path-fallback). Cover slices of auth + propose paths but no full XSS/IDOR/CSRF sweeps.
- **Existing controls audit (preview — full sweep is Tier 0.4):** session cookie attributes are already strong (`HttpOnly`, `SameSite=Strict`, `Secure` when HTTPS, per `php/includes/auth.php:_cookie_params`); CSRF uses double-submit cookie with `hash_equals` constant-time compare (`csrf_token` + `require_csrf` in same file); magic-link tokens are 256-bit `random_bytes(32)` stored as sha256 hash. Tier 0.4 should confirm these and look for gaps.
- **Maintainer auth is currently magic-link.** Per `php/admin.php:130`, statuses are `pending / minimal / full / banned / maintainer / all`. `is_maintainer()` and `require_maintainer()` gate maintainer-only endpoints. There's NO separate maintainer login flow — maintainers use the same `/login.php` magic-link path. The `maintainer_credential` (WebAuthn) table exists but no PHP code references WebAuthn yet (verified via repo-wide grep for `webauthn|publicKey|navigator.credentials`).
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
- Whether maintainer-tier accounts get magic-link only (current), Phase 1b WebAuthn wiring (schema ready), or app TOTP (separate work)
- Log retention duration for `edit_history` and security events
- Whether to offer self-serve account deletion vs manual operator-handled

Each decision shows up at the end of the relevant tier with options + tradeoffs. You make the pick; the next phase assumes that pick.

## Migration tiers

Tier 0 is mandatory + not menu-driven (it produces the inventory the rest of the plan needs). Tiers 1–5 each end with a decision point. Tier 6 closes out with whatever was decided.

### Tier 0 — Threat model + inventory (mandatory)

**Goal:** Produce two artifacts that the rest of the plan reads from.

1. **Phase 0.1 — Surface inventory.** `docs/security/editor-surface.md`:
   - Every PHP entry point that touches editor data (file path + URL): `account.php`, `admin.php`, `auth.php`, `comment.php`, `contact.php`, `edit.php`, `login.php`, `logout.php`, `propose.php`, `review.php` — plus read-only consumers `gauge.php`, `header.php` that show editor-aware UI
   - Every DB table with PII or auth material: `editor` (email, status, last_login_at), `editor_session` (token_hash, ip, user_agent, last_seen_at, revoked_at), `editor_magic_link` (token_hash, ip_issued, expires_at — 30 min, used_at), `maintainer_credential` (WebAuthn credential blob — schema only, not yet wired), `change_request` (proposer + `payload_json` — user-supplied untrusted JSON; rendering it raw would be XSS), `change_request_attachment` (uploads — not yet wired), `edit_history` (changed_by, old/new value)
   - Application-side rate limits to confirm: `comment.php` has `DAILY_CAP=5` per editor per day; `propose.php` per-tier daily caps (per `[project_editor_feature]`: pending=3, minimal=10, full=20); `magic_link_under_throttle()` per-IP + per-email rolling-hour cap
   - Every external service: msmtp → Gmail SMTP relay (per `php/includes/mail.php` docstring); Cloudflare Turnstile (verify endpoint); no third party for analytics or error tracking as of plan-draft date
   - Every cookie set by the editor: `ed_sess` (HttpOnly + SameSite=Strict + Secure when HTTPS, 7-day flat expiry); `ed_csrf` (same flags, session-only); both via `_cookie_params()` in `php/includes/auth.php`
   - File-upload paths: **none yet** — the `change_request_attachment` table exists but no PHP endpoint accepts uploads. Mark as "deferred to Phase 1b wiring."
   - Logging surface: nginx access log (`/var/log/nginx/kayak-access.log` per `deploy/levels:329`) captures full `$request` including magic-link tokens; nginx error log (`/var/log/nginx/kayak-error.log`); PHP error log via PHP-FPM
2. **Phase 0.2 — Data classification.** Tag every column from Phase 0.1 with one of: **public** / **internal** / **PII** / **credential**. PII includes email; credential includes session tokens, magic-link tokens, and any password material.
3. **Phase 0.3 — Threat model.** `docs/security/threat-model.md`. STRIDE-style is fine (Spoofing / Tampering / Repudiation / Information disclosure / Denial of service / Elevation of privilege). For each: who's the realistic threat actor (curious user / disgruntled editor / opportunistic attacker / targeted attacker), and what's the worst-case impact. Skip "nation-state" — proportional threat model only.
4. **Phase 0.4 — Existing-controls map.** For each threat from 0.3, list what's already in place (e.g. "CSRF on edit.php: ✓ via hidden token, see auth.php:142"). Gaps go on the findings list.

**Verification gate (end of Tier 0):**
- Three documents exist (`editor-surface.md`, `threat-model.md`, controls map)
- Findings list has at least 5 entries (if it doesn't, you missed things — go look harder)
- Reviewed with one other set of eyes if practical

### Tier 1 — Authentication review

**Goal:** Confirm magic-link login, session management, and cookie attributes do what they're supposed to. Five objective audit phases plus one decision point at the end (maintainer 2FA model).

1. **Phase 1.1 — Magic-link audit.** **Current state confirmed:** 256-bit tokens via `random_bytes(32)`, stored as sha256 hash, 30-min expiry, single-use (`used_at` set in transaction), CSPRNG-backed. Tier 0.4 will already mark these ✓. **The new audit target:** **the magic-link token IS in the URL query string** (`/auth.php?t=<token>[&next=/path]`) and `$request` is logged by nginx (`deploy/kayak-log-format.conf` + `deploy/levels:329`), so tokens land in `/var/log/nginx/kayak-access.log`. Single-use + 30-min expiry mostly mitigates this (a leaked log token is dead within minutes of consumption), but consider: nginx-side request-URI redaction (`set $clean_request "/auth.php?t=REDACTED"` for matched URIs) and aggressive log rotation. Also confirm Referer leakage: clicking links inside the interstitial post-consumption shouldn't carry the token onward (the POST body should be the only place the token surfaces after the GET).
2. **Phase 1.2 — Session audit.** **Current state confirmed:** `Secure` (when HTTPS) + `HttpOnly` + `SameSite=Strict` (per `_cookie_params()`); session-fixation mitigated (cookie value rotates on login via `set_editor_session()` which generates a fresh `random_bytes(32)`); logout invalidation IS server-side (sets `revoked_at`); 7-day flat absolute timeout, no idle timeout. Audit: confirm `revoked_at` is checked on every session lookup (the `current_editor()` query at line 137 includes `s.revoked_at IS NULL` per grep) and that no PHP code reads cookies after logout. Test: login; capture cookie; logout; replay cookie; expect 401.
3. **Phase 1.3 — Maintainer credential audit.** Today: maintainers use magic-link auth, same as editors (status='maintainer' is the only distinguisher; gating happens via `is_maintainer()` / `require_maintainer()`). The `maintainer_credential` table is provisioned for WebAuthn but Phase 1b wiring (registration + assertion endpoints, JS challenge flow) is NOT done. Audit: (a) confirm magic-link → maintainer-promotion flow is the only access path, (b) assess whether the absence of a stronger second factor is acceptable given the impact (a maintainer-account compromise = arbitrary edits to all reaches + admin UI access), (c) decide whether to advance Phase 1b or formally accept the current posture.
4. **Phase 1.4 — Brute-force / credential-stuffing posture.** **Current state (preview, full audit in this phase):** nginx rate limits ARE in place via `deploy/ratelimit.conf` — `login:1m@3r/min`, `auth:1m@10r/min`, `edit:1m@5r/min`, `contact:1m@10r/min`, `php:10m@5r/sec`, `global:10m@20r/sec`. All zones key on `$binary_remote_addr` — **per-IP only, no per-account lockout**. Turnstile on `/login.php` and `/contact.php`. Audit: confirm zones are wired in `deploy/levels` for the correct locations; assess whether per-IP-only is sufficient (a botnet rotating IPs can grind past); decide whether to add per-account lockout (e.g. on the `editor` row). `editor_magic_link` rate limit is enforced application-side via `magic_link_under_throttle()` (per `tests/php/AuthTest.php`).
5. **Phase 1.5 — Account-recovery flow.** Forgotten-magic-link handling (any cap on resends? `magic_link_under_throttle` — verify cap windows). Email-changed handling. Account-takeover via email compromise blast radius. **Email-normalization weakness:** `normalize_email()` in `php/includes/auth.php` is just `strtolower(trim(...))` — Gmail's `Foo.Bar+test@gmail.com` and `foobar@gmail.com` resolve to *different* editor rows. An attacker can create multiple alias accounts to bypass per-account limits or to confuse audit logs. Decide whether to normalize Gmail-style aliases or accept the current behavior. **`safe_next_url()` open-redirect protection** is already in place (rejects URLs that don't start with `/<non-slash>`); audit it for adequacy (`//evil.com` and `/\evil.com` blocked; what about `/path/?redirect=https://...`).

**Verification gate (end of Tier 1):**
- Each of the above tested with a written log of pass/fail/N/A
- Failures filed as findings
- Mitigation effort estimated for each finding

**Decision point — maintainer 2FA model:**
- **Magic-link only** (current). Cheapest, no UX friction, but compromised email = compromised account. Acceptable if the maintainer email is itself well-protected (its own 2FA, recent audits).
- **Advance Phase 1b WebAuthn wiring** (passkeys via the existing `maintainer_credential` schema). The DB is ready; the work is JS challenge flow + 2-3 PHP endpoints (registration, assertion, list/revoke). No hardware purchase if the maintainer uses a phone or laptop's built-in passkey support; YubiKey adds defense-in-depth. Strongest option since the schema is already designed for it.
- **TOTP via app** (Authy/Google Authenticator). Standard fallback. Would need a new DB column; QR setup flow. Less work than WebAuthn but also less protection (TOTP is phishable; WebAuthn isn't).

### Tier 2 — Authorization review

**Goal:** Confirm editors can't see or modify what they shouldn't; maintainers can't be impersonated by editors.

1. **Phase 2.1 — Role enforcement audit.** Every endpoint that requires editor-or-maintainer status: is the check present? Is it consistent (helper function, not ad-hoc)? Test: hit each endpoint without auth, with editor cookie, with maintainer cookie; expect 401/200/200 respectively.
2. **Phase 2.2 — IDOR sweep.** Inventory of ID-taking endpoints (from a plan-draft grep — re-verify in Phase 0.1):
   - GET id: `api.php`, `description.php` (+ `hidden`), `gauge.php`, `latest.php`, `plot.php` (+ `days`, `embed`), `edit.php` (+ POST `reach_id`/`gauge_id`), `propose.php` (+ POST `target_id`)
   - Watch for the GET vs POST param-name discrepancy in `edit.php` (`?id=` vs `reach_id=` body) and `propose.php` (`?id=` vs `target_id=` body) — IDOR audits often miss the POST body.
   For each: does it verify the requester is allowed to access that specific row? Test: request your own object; record the URL; replay it from a different account; expect 403. For the read-only `description.php`/`gauge.php`/etc., "allowed" may mean "any reach is public" — document that as the deliberate model so future audits don't re-flag it.
3. **Phase 2.3 — Privilege escalation paths.** Can an editor promote themselves to maintainer through any code path? Mass-assignment in update endpoints? File upload that writes to a privileged location? SQL injection that lets you insert into `maintainer_credential`?
4. **Phase 2.4 — Audit trail integrity.** Per-row schema (`src/kayak/db/models.py:836`): `target_type`, `target_id`, `field`, `old_value`, `new_value`, `changed_at`, `changed_by` (`'maintainer:<id>'` or `'editor:<id>'`). **No hash chain, no previous_hash, no external sink** — current state. Anyone with DB write access (i.e. you, the operator, or anyone who breaches the maintainer cookie + admin endpoints + a separate path to write SQL directly) can `DELETE FROM edit_history WHERE id=N`. The "currently" answer to all three audit questions is "no protection beyond DB access controls." This sets up the Tier 2 decision point.

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

1. **Phase 3.1 — XSS sweep.** Files using `htmlspecialchars` (a quick proxy for "thinking about XSS"): account, auth, contact, custom_gauges, data, gauge, logout, plot, propose, review. Files NOT obviously using it but emitting HTML: **admin.php, comment.php, custom.php, description.php, edit.php, login.php, picker.php, reach.php** — these are the priority audit targets. Test: submit `<script>alert(1)</script>` everywhere accepting input; visit pages that render it; expect no execution. Confirm `htmlspecialchars` calls use `ENT_QUOTES | ENT_HTML5` for HTML5 attribute contexts.
2. **Phase 3.2 — SQLi sweep.** Every PDO call: parameterized? No string concatenation? PHPStan should flag the obvious cases. Manual review for the rest.
3. **Phase 3.3 — File-upload audit.** **Currently N/A — feature not yet wired.** The `change_request_attachment` table exists (`filename`, `content_type`, `size_bytes`, `sha256`, `storage_path`, `caption`) per the schema in `src/kayak/db/models.py:804`, with sha256-content-addressed storage under "a dedicated uploads root." But `grep -rn "move_uploaded_file|\$_FILES" php/` returns nothing — no PHP endpoint accepts uploads as of plan-draft date. **When the upload endpoint lands**, this phase activates: audit MIME validation, max-size enforcement, filename sanitization (`storage_path` based on sha256 should sidestep path traversal but verify), what nginx serves the uploads root with (no PHP execution; explicit `Content-Type`; `add_header Content-Disposition attachment` if user-uploaded), and whether the uploads root has the execute bit stripped from files.
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
3. **Decision point — retention of audit/PII tables:**
   - **`edit_history`:** indefinite (current); or N years then anonymize the actor (`changed_by` column). Edit content stays; "who" is dropped after some retention.
   - **`editor_magic_link.ip_issued`:** indefinite (current — IP captured per-issuance as IPv6-capable VARCHAR(45)); or auto-purge rows older than expiry + N days. The IP is useful for incident forensics but rarely beyond a few weeks.
   - **`editor_session.ip` and `editor_session.user_agent`:** same question. Useful while session is live + a short tail; less so after expiry.
4. **Decision point — privacy policy + terms:**
   - **None** (current). Defensible for a hobby site; weak if someone formally demands.
   - **`/privacy.html` + `/terms.html`** boilerplate. ~1 hour to draft; sets expectations; minimal commitment.
   - **Lawyer-reviewed**. Days of work + cost; appropriate if revenue or specific obligations.
5. **Decision point — `security.txt`:** A published `security.txt` already exists at `static/security.txt` (RFC 9116 minimum: `Contact: mailto:pat.kayak@gmail.com`, `Expires: 2027-05-20T00:00:00Z`, `Preferred-Languages: en`), served via the nginx alias at `/.well-known/security.txt` per `deploy/levels`. Decision menu becomes:
   - **Keep current** (Contact + Expires only). Defensible for a hobby site; commits you to read the gmail address.
   - **Add `Encryption: <https://...key.asc>`** + post a PGP key. Marginally more secure for the discloser; modestly more work.
   - **Add `Acknowledgments:` + `Policy:`** lines. Sets disclosure-process expectations explicitly. The `Policy:` URL would point to a page describing your IR cadence (Tier 5 decision).
   - **Refresh `Expires:`** annually as part of the Tier 5 re-review cadence; current expiry is in ~1 year so this is the next maintenance trigger.

**Verification gate (end of Tier 4):**
- Each decision point has a written choice + rationale in `docs/security/decisions.md`
- Anything chosen is implemented (Tier 6) or explicitly deferred with a date

### Tier 5 — Disclosure + response

**Goal:** Decide how vulnerabilities reach you, what you commit to doing about them, and how often the whole posture is re-checked.

1. **Decision point — vulnerability disclosure path:**
   - **`security.txt` only** (current — Tier 4.5 already in place). Anyone who finds a bug emails `pat.kayak@gmail.com`. Default; no action needed unless you want more.
   - **GitHub Security Advisories** as a second channel. Coordinated disclosure via GitHub. Free; assumes the codebase is open-source-ish (this repo is mousebrains/kayak_python — already on GitHub). Add `Policy:` URL in security.txt pointing at your GHSA flow.
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
5. **Phase 5.5 — Backup-restore drill from a security angle.** The Tier 4.4 production-discipline drill restores from backup for *availability*; this version restores assuming the live DB is *poisoned* (attacker-modified rows). Existing infrastructure to leverage: `scripts/db_pull.sh` (pulls from prod), `docs/db_sync.md` (the documented pull/push procedure — note: per `[feedback_never_run_db_push]`, `db_push.sh` is dev-only; never run on the prod machine), `docs/offsite-backup.md` (rclone crypt → Drive). Drill: choose a date in the past; compute "what's lost between then and now" (count of `edit_history` rows, count of `change_request` rows); restore to a temp copy; verify the data integrity; document the gap. Do NOT swap into live without explicit confirmation per `[feedback_never_overwrite_db]`.

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

# Rate-limit zones (Phase 1.4)
cat deploy/ratelimit.conf
grep -n "limit_req zone" deploy/levels

# CSP / nginx surface for editor endpoints (sudo only — snippets aren't in repo)
sudo nginx -T 2>/dev/null | grep -B2 -A8 "/contact.php\|/edit.php\|/propose.php\|/review.php\|Turnstile\|Content-Security-Policy"

# Turnstile integration locations
grep -rn "turnstile\|Turnstile" php/ src/ 2>/dev/null

# File-upload paths (Phase 3.3 — currently expected empty)
grep -rn "move_uploaded_file\|\\\$_FILES" php/ 2>/dev/null

# WebAuthn implementation status (Phase 1.3 — currently expected empty in php/)
grep -rln "navigator.credentials\|publicKey\|webauthn" php/ src/ 2>/dev/null

# nginx access-log captures full $request including magic-link tokens (Phase 1.1)
cat deploy/kayak-log-format.conf

# Cookie attribute helper (Phase 1.2)
grep -A 10 "_cookie_params" php/includes/auth.php | head -15

# Magic-link expiry constant (Phase 1.1)
grep -B1 -A 3 "30 \* 60\|MAGIC_LINK_TTL" php/includes/auth.php

# Application-side daily caps (Phase 0.1 inventory)
grep -n "DAILY_CAP\|daily_cap\|TIER_DAILY" php/comment.php php/propose.php

# Existing security docs (likely none)
ls docs/security/ 2>/dev/null
```
