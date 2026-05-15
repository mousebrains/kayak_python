# Editor pipeline threat model

> **Drafted:** 2026-05-12 against `main` at `86cfa9a`. Part of `docs/done/PLAN_editor_security_review.md` Tier 0.3. STRIDE-style; proportional (nation-state actors out of scope).
>
> Read [editor-surface.md](editor-surface.md) first for the asset inventory and component map. [controls-map.md](controls-map.md) maps each threat below to existing mitigations and gaps; gaps become entries in [findings.md](findings.md).

## Actors

Three realistic actor profiles. "Nation-state" is excluded per the plan; "insider with shell access" is conflated with operator (you).

| Actor | Capabilities | Motivation |
|---|---|---|
| **Curious user** | Anonymous web access; can register an editor account; reads HTML; uses browser DevTools. No PHP/SQL knowledge presumed. | Idle exploration; curiosity about other proposals or other editors. |
| **Disgruntled editor** | Has an `editor` row in `status='minimal'` or `'full'`. Knows the propose/comment flow. May read JS source. | Vandalism, harassment, fraud (impersonating others), self-promotion to maintainer, retaliation after status downgrade or ban. |
| **Opportunistic attacker** | Automated scans, mass credential stuffing, common-exploit kits (XSS, SQLi, SSRF probes). No targeted recon of this specific site. | Site compromise for spam relay / SEO injection / botnet conscription. |
| **Targeted attacker** | Knows this site; reads `static/security.txt`; manually probes the editor pipeline. Plausibly has done OSINT on the operator. | Defacement of reach data, exfiltration of editor emails (PII), real persistent compromise. |

**The operator (Pat)** has shell access, root via sudo, DB direct access, and Hetzner console. The threat model assumes the operator is not adversarial; insider-threat scenarios are out of scope (it's a one-person hobby project).

## Assets at risk (recap)

From [editor-surface.md](editor-surface.md):

| Asset | Class | Worst-case loss |
|---|---|---|
| `editor.email` (all editors) | PII | doxxing of contributors; phishing fuel |
| `editor_session.token_hash` | credential | session takeover (7-day window) |
| `editor_magic_link.token_hash` | credential | one-time login takeover (30-min window) |
| `maintainer_credential.*` | credential (not yet wired) | n/a today; would be maintainer-account takeover if wired |
| `change_request.payload_json` | internal (user-supplied, untrusted) | XSS vector if rendered raw; reputational if abused |
| `reach.*` / `gauge.*` (via maintainer or approval) | public (data correctness) | misleading river-level info; safety implication for paddlers |
| `edit_history` | internal | repudiation of who changed what; no tamper-resistance today |
| `ed_sess` cookie | credential (in transit + at rest) | session takeover |
| nginx access log | logging surface | magic-link token capture for unconsumed tokens |

## STRIDE threats

Notation: `[T-Xn]` is a stable id used by [controls-map.md](controls-map.md) to cross-reference. Impact is **L** (low) / **M** (medium) / **H** (high) / **C** (critical).

### S — Spoofing

| Id | Threat | Actors | Impact | Notes |
|---|---|---|---|---|
| **T-S1** | Magic-link token theft via nginx access log disclosure | targeted, opportunistic (if log breach) | M (single account, 30-min window minus consumption) | Token in `$request`; single-use + 30-min expiry mitigate. Worst case: an attacker with read-only log access can rip newly-issued tokens before the user clicks. |
| **T-S2** | Magic-link token theft via email account compromise | targeted | C | Email account is the magic-link delivery channel. Compromise = full editor (or maintainer) account takeover. Same-day account recovery would re-issue. |
| **T-S3** | `ed_sess` cookie theft via XSS | opportunistic, targeted | H | HttpOnly defeats `document.cookie`; would require browser-context exfil (e.g. via fetch attack from injected JS). |
| **T-S4** | `ed_sess` cookie theft via shared/stolen device | curious | M | SameSite=Strict + 7-day flat expiry. No per-session IP binding, so cookie lifts cleanly if exfiltrated. |
| **T-S5** | CSRF attack against state-changing endpoint | targeted | M | Double-submit cookie with `hash_equals` constant-time compare. Requires same-origin XSS to forge from inside the SameSite boundary. |
| **T-S6** | Gmail-style alias spoofing (`Foo.Bar+x@gmail.com` → multiple accounts) | disgruntled editor | M | `normalize_email()` is `strtolower(trim(...))`; doesn't strip `+tag` or dots. Attacker spawns N accounts to bypass per-account daily caps and to dilute audit trail. Same human, multiple `editor.id` rows. |
| **T-S7** | IP spoofing to bypass nginx `limit_req` per-IP zones | opportunistic | L | TCP handshake required; nginx sees real client IP unless an intermediate proxy (none) terminates. CF in front (if active) passes real IP via `X-Forwarded-For`; risk shifts to spoofed XFF. |

### T — Tampering

| Id | Threat | Actors | Impact | Notes |
|---|---|---|---|---|
| **T-T1** | Stored XSS via `change_request.payload_json` | disgruntled, opportunistic | H | A proposal renders in `review.php` (maintainer-facing) and may be reflected back to the editor at `account.php`/`propose.php`. If any path renders without `htmlspecialchars`, the maintainer's session is at risk. |
| **T-T2** | Direct DB tampering of `edit_history` | operator (out of scope); maintainer with shell | H if it happens | No hash chain, no append-only journal, no external sink. Anyone who can run SQL can `DELETE`/`UPDATE` rows without leaving evidence. |
| **T-T3** | Mass-assignment in `account.php` / `edit.php` / `propose.php` (e.g. `display_name=…&status=maintainer`) | disgruntled, targeted | C | Each handler must validate which fields it accepts and reject unknown POST keys. PHP doesn't auto-bind, but the per-handler loop must be defensive. |
| **T-T4** | SQL injection on any unparameterized PDO call | opportunistic, targeted | C | Repo convention is parameterized prepare/execute; PHPStan should catch obvious cases. One overlooked concat lets the attacker promote themselves or read all session tokens. |
| **T-T5** | Race in `review_approve` (T-OCTOU between read-state and write-state) | disgruntled, targeted | M | `tests/php/ReviewApproveRaceTest.php` covers a concurrent-approval scenario. Audit gate: confirm other state-transition flows don't have the same shape. |
| **T-T6** | Maintainer-side: tweak `applied_json` to write fields outside the approval scope | disgruntled (if promoted) | M | `review.php` allows the maintainer to edit values before approving; what's the safeguard against approving a payload that names fields outside the proposer's tier scope? |

### R — Repudiation

| Id | Threat | Actors | Impact | Notes |
|---|---|---|---|---|
| **T-R1** | Editor denies submitting a proposal | disgruntled | L | `change_request.editor_id` + `submitted_at` + nginx access log + msmtp log triangulate; reasonable proof for an honest dispute. |
| **T-R2** | Maintainer denies an approval (or an editor blames a maintainer impersonator) | disgruntled, targeted | M | `edit_history.changed_by='maintainer:<id>'` records who. But [T-T2] (DB-side tampering) breaks this — anyone with SQL access can rewrite. No external corroboration sink. |
| **T-R3** | Magic-link token "I didn't request it" | curious, targeted | L | `editor_magic_link.ip_issued` + `created_at` + nginx access log. Unhelpful if the attacker spoofed the email and the victim never received it (asymmetric scenario; treat as out of scope). |

### I — Information disclosure

| Id | Threat | Actors | Impact | Notes |
|---|---|---|---|---|
| **T-I1** | Editor reads another editor's proposals via IDOR | curious, disgruntled | M | Plan flags `propose.php` POST `target_id` + `review.php` GET `id` as IDOR-sweep targets. `propose.php` is per-editor (you propose against a target *reach*; the change_request row is yours); `review.php` is maintainer-only. The IDOR test is whether non-maintainer can hit `review.php?id=X` and see contents. Expected behavior is 403. |
| **T-I2** | Editor reads another editor's PII (email) | curious, disgruntled | M | `admin.php` is maintainer-only. `account.php` shows only the current editor's own. Audit gate: confirm `payload_json` doesn't echo other editors' emails. |
| **T-I3** | Session-token disclosure via log breach | opportunistic | L | sha256-stored in DB; nginx access log captures `$request` not headers, so `Cookie:` doesn't land in the log. PHP-FPM error log could conceivably emit a cookie if a debug-log call were ever added; current code doesn't. |
| **T-I4** | Magic-link token capture in log (same as T-S1 but framed as disclosure) | opportunistic, targeted | M | Cross-listed with T-S1; controls overlap. |
| **T-I5** | Error-message leakage (stack traces, SQL, paths) | curious, opportunistic | M | PHP-FPM `display_errors` must be off in prod; `expose_php=Off`. Audit gate confirms. |
| **T-I6** | nginx error log accessibility | opportunistic | L | Log file mode/owner. Should be `nginx:adm 640` or similar; not world-readable. Audit gate. |
| **T-I7** | Backup leak (Hetzner backup, rclone bucket) | targeted | H | Backups contain the full DB incl. email + session/token hashes. rclone crypt should be in place per `docs/offsite-backup.md`. |
| **T-I8** | edit_history rendered to non-maintainer | curious, disgruntled | L | Inventory says no entry point renders edit_history; verified by grep. Audit gate confirms still true. |

### D — Denial of service

| Id | Threat | Actors | Impact | Notes |
|---|---|---|---|---|
| **T-D1** | Magic-link mailbombing (target inbox flood) | targeted | M | `magic_link_under_throttle` caps at 5/email/hour, 20/IP/hour. Botnet rotating IPs grinds per-IP, but per-email cap of 5/hr is the binding constraint on a single victim. |
| **T-D2** | Proposal/comment table fill via multiple-alias accounts | disgruntled | M | Compounded by [T-S6]; an attacker with N Gmail aliases gets N × tier-cap proposals/day. Disk-fill scenario: 10000 chars × 100 accounts × 20 proposals/day × 365 days = ~7 GB/yr. Not immediate concern; long-tail. |
| **T-D3** | Review-queue overload | disgruntled | L | Review queue is a UI concern; even thousands of pending rows don't break the page (paginated; only `pending` shown by default). |
| **T-D4** | nginx-level flood (legitimate path overrun) | opportunistic | M | `global` zone 20 r/s + 40 burst per IP. Distributed flood needs CF-level protection (not present). |
| **T-D5** | Filling logs (10000 char body × many requests) | disgruntled | L | nginx truncates `$request` to 8K by default; bodies aren't logged. Mostly a non-threat. |
| **T-D6** | PHP-FPM worker exhaustion via slow requests | targeted | M | No per-request timeout audit in scope today; nginx `proxy_read_timeout` and `fastcgi_read_timeout` defaults apply. |

### E — Elevation of privilege

| Id | Threat | Actors | Impact | Notes |
|---|---|---|---|---|
| **T-E1** | Editor promotes self to maintainer via mass-assignment ([T-T3] specialization) | disgruntled | C | Highest-impact tampering scenario. |
| **T-E2** | Editor promotes self via SQLi ([T-T4] specialization) | disgruntled, targeted | C | Same vector, different mechanism. |
| **T-E3** | Promotion via WebAuthn endpoint when wired | n/a today | n/a | Wired = needs full audit (Phase 1.3 decision). |
| **T-E4** | Session-cookie swap from editor to maintainer | curious, disgruntled | L | sha256-hashed token; can't be forged without the raw cookie value. Cookie is HttpOnly + SameSite=Strict. |
| **T-E5** | `next_url` open-redirect to attacker site for credential phishing | targeted | M | `safe_next_url()` rejects `//evil.com` and `/\evil.com`; audit gate is whether subtler bypasses exist (e.g. `/path?redir=https://...` where downstream code follows the param). |
| **T-E6** | Privilege escalation via approving own proposal | disgruntled (if promoted) | M | Self-approval: maintainer reviews their own proposal? `review.php` would need to forbid; if doesn't, a freshly-promoted maintainer auto-approves their backlog. |
| **T-E7** | Privilege escalation via approval of proposal that names out-of-tier fields | disgruntled | M | Cross-listed with [T-T6]; the proposer is tier-gated but the approver picks what to apply. |

## Priority matrix

Sorting by (Impact × Likelihood) — likelihood is rough qualitative.

| Priority | Threats |
|---|---|
| **Critical** (act fast; no compensating control) | T-T3 / T-E1 (mass-assignment), T-T4 / T-E2 (SQLi), T-S2 (email compromise) |
| **High** (verify control; fix if absent) | T-T1 (stored XSS), T-S3 (XSS-mediated session theft), T-I7 (backup leak) |
| **Medium** (verify; defensible to defer) | T-S1 / T-I4 (magic-link in log), T-T6 (over-scope approval), T-D1 (mailbomb), T-D6 (slow-loris), T-E5 (open-redirect), T-E6 (self-approve), T-E7 (over-tier apply), T-I1 (IDOR on review), T-I5 (error leak), T-R2 (audit-trail repudiation) |
| **Low** (note, deprioritize) | T-S4 (stolen device), T-S5 (CSRF — strong control), T-S7 (IP spoof), T-R1 (proposal denial), T-R3 (link denial), T-I3 (cookie in log), T-I6 (error log perms), T-I8 (edit_history exposure), T-D2 (long-tail fill), T-D3 (queue overload), T-D4 (nginx flood), T-D5 (log fill), T-E4 (session swap) |

[controls-map.md](controls-map.md) takes each threat and pairs it with the existing control or marks it as a finding.

## Cross-reference to plan's existing findings

Plan-doc seeds (from `docs/done/PLAN_editor_security_review.md` Constraints + Phase 1.1/1.5 notes) map to:

- "Magic-link token in URL" → **T-S1 / T-I4**
- "HSTS may not be enabled" → adjacent to **T-S3** (HSTS reduces session-cookie capture risk on first-load HTTP→HTTPS)
- "`normalize_email()` doesn't strip Gmail aliases" → **T-S6**
- "`edit_history` has no tamper-resistance" → **T-T2 / T-R2**
- "Maintainer auth is currently magic-link" → makes **T-S2** worse for maintainer accounts vs editor accounts (impact ↑ C)

Nothing in the plan currently surfaces:

- T-T6 over-scope approval
- T-E6 self-approval
- T-E7 over-tier apply
- T-D6 slow-loris (PHP-FPM worker exhaustion)
- T-I7 backup leak (covered by separate offsite-backup.md plan but not by this security review)

Tier 0.4 controls-map will surface whether these are real gaps or already-mitigated; if real, they become findings.
