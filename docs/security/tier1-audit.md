# Tier 1 — Authentication review audit log

> **Started:** 2026-05-12 against `main` at `21c9e1a`. Per `docs/PLAN_editor_security_review.md` Tier 1 verification gate: "Each of the above tested with a written log of pass/fail/N/A; failures filed as findings; mitigation effort estimated for each finding."
>
> Verdict legend: ✅ pass / ⚠ partial / ❌ fail / ⊘ N/A.

## Phase 1.1 — Magic-link audit

**Verdict:** ⚠ (4 pass, 1 partial — F-2 confirmed and refined; one new finding F-14 surfaced)

### Audit tests

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 1.1.1 | Token is CSPRNG-backed and full-entropy | ✅ | `generate_token()` in `php/includes/auth.php` returns `bin2hex(random_bytes(32))` — 256-bit entropy. |
| 1.1.2 | Token stored as hash at rest | ✅ | `editor_magic_link.token_hash` populated via `hash_token($tok)` → `hash('sha256', $tok)`. Raw token never persisted to DB. |
| 1.1.3 | 30-min absolute expiry enforced | ✅ | `expires_at` set at insertion; consumed only when `expires_at > datetime('now')`. Verified in `peek_magic_link()` and `consume_magic_link()`. |
| 1.1.4 | Single-use enforced atomically | ✅ | `consume_magic_link()` runs SELECT + UPDATE inside `beginTransaction()/commit()`. SQLite serialization prevents double-consume races. |
| 1.1.5 | GET/POST split prevents email-scanner prefetch | ✅ | `php/auth.php:7-13` documents the design. GET calls `peek_magic_link()` (no consumption); POST calls `consume_magic_link()`. Outlook Defender / Proofpoint prefetch GET, see the form, leave the token alone. |
| 1.1.6 | CSRF required on POST consumption | ✅ | `php/auth.php:34` calls `require_csrf()`. CSRF cookie set lazily by `csrf_token()` during the GET interstitial render. |
| 1.1.7 | Redirect target validated | ✅ | `safe_next_url()` called on both GET and POST paths (`php/auth.php:36`, `:45`, `:53`); rejects external URLs and path-traversal patterns. |
| 1.1.8 | No browser caching of `/auth.php` | ✅ | `header('Cache-Control: no-store')` set at `php/auth.php:21`. |
| 1.1.9 | Email body is plain-text only (no HTML) | ✅ | `render_magic_link_email()` returns a heredoc plain-text string; `Content-Type: text/plain; charset=UTF-8` set in `send_email()`. Minimizes email-client attack surface. |
| 1.1.10 | Token in URL → nginx access log | ⚠ | **F-2 confirmed.** nginx log format includes `$request` (`deploy/kayak-log-format.conf`); `access_log` directive in `deploy/levels:329` writes to `/var/log/nginx/kayak-access.log`. Magic-link URL is constructed in `php/login.php:51`: `https://levels.wkcc.org/auth.php?t=<token>&next=...`. Token lands in log via `$request` field. |
| 1.1.11 | Referer leakage post-consumption | ⚠ | **F-14 (new).** After POST-consume, browser follows the 302 to `$next`. Referer header on that follow-up request is the previous URL — i.e. `/auth.php?t=TOKEN`. Subsequent same-origin requests (loading `/static/leaflet.js`, etc.) also carry this Referer. nginx logs `$http_referer` (see log format), so the token is captured twice: once in `$request` on initial GET, once in `$http_referer` on each post-consume request until the user navigates away. No `Referrer-Policy` header set on `/auth.php` response. |

### Findings refinement

- **F-2** (existing): kept; specifically the `$request`-field exposure on the initial GET. Mitigation options listed in `findings.md`.
- **F-14** (new): Referer leakage in `$http_referer` after consumption. Mitigation: set `Referrer-Policy: no-referrer` on `/auth.php` HTTP response (one-line `header()` call). Lower effort than F-2 mitigation; orthogonal.

### Effort estimate

| Finding | Mitigation | Effort |
|---|---|---|
| F-2 | nginx log-format redaction via `map` directive on `/auth.php?t=…` → `?t=REDACTED` in `deploy/kayak-log-format.conf` | ~1h: edit + test on staging + restart nginx + verify log shows redaction |
| F-2 (alt) | Switch magic-link consumption to numeric-code-in-email (no URL) | ~1d: UX redesign, new form, retain GET/POST split for the code-entry page |
| F-14 | `header('Referrer-Policy: no-referrer')` on `auth.php` and ideally `set_editor_session()`'s response context | ~15min: one-line edit + restart-and-verify |

### Notes

- The realistic attack window for F-2 is `email-send → user-click`. If the user clicks within 30 seconds, an attacker reading the access log has ~30 seconds to consume. If the user doesn't click for 25 minutes, the attacker has ~25 minutes. Single-use + 30-min expiry prevent compound exploitation but don't shrink the per-token window.
- The plan flagged F-2 as "consider nginx-side request-URI redaction and aggressive log rotation." Redaction is cleaner; rotation is a partial mitigation only.
- The CSRF requirement on POST doesn't defeat an attacker who controls both GET and POST (e.g., reading log → GETting `/auth.php?t=…` from their own browser to obtain the CSRF cookie, then POSTing). CSRF defends against cross-origin forgery, not log-leak replay.

### Phase 1.1 closeout

- ✅ Audit completed; tests written.
- ⚠ Two open findings: F-2, F-14.
- Mitigation effort estimated for each.

## Phase 1.2 — Session audit

**Verdict:** ✅ (9 pass, 1 informational gap → F-15)

### Audit tests

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 1.2.1 | Cookie `HttpOnly` flag set | ✅ | `_cookie_params()` returns `'httponly' => true`. |
| 1.2.2 | Cookie `SameSite=Strict` set | ✅ | `'samesite' => 'Strict'`. |
| 1.2.3 | Cookie `Secure` flag set when HTTPS | ✅ | `'secure' => !empty($_SERVER['HTTPS'])`. Confirmed prod uses HTTPS (per `deploy/SETUP.md`). |
| 1.2.4 | Session token rotates on login (session-fixation defense) | ✅ | `set_editor_session()` calls `generate_token()` → fresh `random_bytes(32)` for every login. Old pre-auth `ed_sess` (if any) replaced by setcookie. |
| 1.2.5 | CSRF token rotates on session creation (privilege-escalation fixation defense) | ✅ | `set_editor_session()` generates a fresh CSRF token and overwrites `ed_csrf`. |
| 1.2.6 | Logout invalidates server-side | ✅ | `clear_editor_session()` UPDATE `editor_session SET revoked_at = datetime('now') WHERE token_hash = ?`. Cookie also cleared client-side. |
| 1.2.7 | `current_editor()` filters revoked sessions | ✅ | SQL clause `s.revoked_at IS NULL` in the session lookup. Revoked cookie cannot resurrect. |
| 1.2.8 | `current_editor()` filters expired sessions | ✅ | SQL clause `s.expires_at > datetime('now')`. 7-day flat absolute timeout. |
| 1.2.9 | `current_editor()` excludes banned editors | ✅ | SQL clause `e.status != 'banned'`. |
| 1.2.10 | No code reads `EDITOR_SESSION_COOKIE` outside `auth.php` helpers | ✅ | grep across `php/` shows 4 reads, all in `php/includes/auth.php` (set, clear, current_editor). No bypass paths. |
| 1.2.11 | Login → capture cookie → logout → replay → 401 | ⚠ **F-15** | Static analysis says this MUST hold (revoked_at filter + cookie clear). No automated test covers it in `tests/php/*.php` (grep for `logout|revoke|revoked` returns empty). Live integration test recommended once. |

### Findings refinement

- **F-15** (new): no automated regression test for logout-then-replay. Pure test-coverage gap, not a real vulnerability — the SQL filter already enforces this. Filing because the verification gate is "Each test pass/fail/N/A," and one of the plan's listed tests is uncovered.

### Effort estimate

| Finding | Mitigation | Effort |
|---|---|---|
| F-15 | Add a method to `tests/php/EditAuthTest.php` (or new `tests/php/SessionRevocationTest.php`): seed an editor + session row, fetch via `current_editor()` (expect editor), call `clear_editor_session()` with the seeded cookie, fetch again (expect null). | ~30 min: bootstrap.php needs `editor_session` table addition; test body ~30 lines. |
| F-15 (alt — live test on staging) | One-time manual test using a real browser, recorded in this audit log | ~5 min, no code change. Acceptable for a hobby project; doesn't catch future regressions. |

### Notes

- **`current_editor()` request-scoped cache** (`static $cached = false; static $editor = null;`) is safe — per-request only. A logout occurring mid-request would not affect the in-flight cached result, but in-flight pages can't be logged out without browser-side intervention anyway.
- **No idle timeout** — sessions live 7 days even if unused. This is a deliberate choice per the plan ("7-day flat absolute timeout, no idle timeout") trading off security for UX (long-tail mobile usage). Stolen-cookie blast radius is 7 days; would need re-issue cadence + IP-stickiness or shorter absolute timeout to reduce. Not filing as a finding — the plan accepts this.
- **No per-session IP binding** (also deliberate — mobile/laptop roaming). Acceptable risk; documented.

### Phase 1.2 closeout

- ✅ Audit completed; all controls structurally sound.
- ⚠ 1 informational gap: F-15 (test coverage, not vulnerability).
- Static analysis confirms the plan's listed test (logout-replay-401) is enforced by the SQL filter; no live exploit demonstrated or expected.

## Phase 1.3 — Maintainer credential audit + 2FA decision

**Verdict:** ✅ (stronger than expected — there is NO web path to maintainer promotion; the only path is the CLI `levels seed-maintainer`). 2FA decision below.

### Audit (a) — Is magic-link the only access path to maintainer status?

Surveyed all `UPDATE editor SET status` calls in `php/`:

| File:line | Action | Target status | Source status (guard) |
|---|---|---|---|
| `php/admin.php:35-39` | bulk_approve | `minimal` | `pending` only |
| `php/admin.php:44-50` | promote | `full` | `pending` or `minimal` |
| `php/admin.php:53-59` | approve_minimal | `minimal` | `pending` only |
| `php/admin.php:62-68` | demote | `minimal` | `full` only |
| `php/admin.php:71-78` | reset_pending | `pending` | `minimal` or `full` only |
| `php/admin.php:80-90` | ban | `banned` | NOT `maintainer` |
| `php/admin.php:93-99` | unban | `pending` | `banned` only |

**No admin action sets `status = 'maintainer'`.** The ceiling via web is `'full'`. The ONLY path to `'maintainer'` status is the CLI command `levels seed-maintainer --email <email>` (registered in `src/kayak/cli/seed_maintainer.py`), which requires shell access on the prod box.

**This is a stronger control than the plan anticipated.** Web-side compromise (even maintainer-account-takeover) cannot elevate a different account to maintainer. A maintainer takeover lets the attacker BAN other maintainers (`ban` action permits `'full' → 'banned'` but not `'maintainer' → 'banned'`; line 84 guards `status != 'maintainer'`) but cannot CREATE new maintainers via web.

### Audit (b) — Impact of magic-link-only auth for existing maintainer accounts

A maintainer-email compromise yields:

- Full edit access to all reaches/gauges via `/edit.php` (write to `reach`/`gauge` tables + `edit_history`).
- Admin UI access via `/admin.php` (promote/demote/ban non-maintainer editors; revoke sessions; bulk-edit display names).
- Approve/reject/edit pending proposals via `/review.php` (write to `change_request` + `edit_history`).
- Read all editor PII (emails) via `/admin.php`.
- Bypass all rate limits / daily caps (the entire enforcement model is editor-status-gated).
- Tamper `edit_history` indirectly by approving a backdoored proposal (limited — `applied_json` only writes back to live tables, doesn't delete rows; but F-4 says no protection against post-hoc rewrite via SQL access).

Impact rating: **Critical** for the editor pipeline, but **bounded** — the attacker still cannot:

- Create another maintainer account (CLI-only).
- Read raw session tokens (only sha256 hashes in DB).
- Access non-editor-pipeline parts of the system without shell access.

### Audit (c) — 2FA decision

Three documented options (decision menu from `docs/PLAN_editor_security_review.md` Tier 1.3):

| Option | Cost | Strength | UX cost | Notes |
|---|---|---|---|---|
| **A. Magic-link only (current)** | $0 | Single factor; email-account is the de facto 2FA. | None | Relies on maintainer's email account having strong 2FA itself. Operator-managed gate (you control which emails are seeded as maintainer). |
| **B. Advance Phase 1b WebAuthn** | ~2-3 PHP endpoints + JS challenge flow (registration, assertion, list, revoke). DB schema in place (`maintainer_credential`). | Phishing-resistant; device-bound credential. Modern browsers + iOS/Android support built-in passkeys. | First-time enrollment 30s; subsequent auth one tap on phone/laptop bio. | Strongest practical option. ~1-2 days of work. |
| **C. TOTP via authenticator app** | New DB column (totp_secret); QR setup flow; verify endpoint. | Time-based code; phishable; replay-resistant only within ~30s window. | App install + 6-digit code on each login. | Less work than WebAuthn but lower security. |

### Recommendation

**Option A (magic-link only)** for the next 6-12 months, **conditional on:**
1. The single maintainer (pat.kayak@gmail.com) confirms their Gmail account has 2FA enabled (TOTP or hardware key).
2. Re-evaluate when a second maintainer is added (one-person operation simplifies threat model; multi-person operation increases attack surface).
3. Re-evaluate if any audit-trail integrity work happens (F-4) — strong audit + weak auth is an inconsistent posture.

**Rationale:** the seed-maintainer CLI control is unusually strong. The realistic remaining attack on the magic-link path requires (a) Gmail compromise AND (b) being targeted enough to know the maintainer email AND (c) acting within the 30-min link-expiry window. Probability is low for a hobby/club site. Option B is the right answer if a second maintainer joins; Option C is dominated by B (more work, less security).

### Findings refinement

- **F-5 confirmed**: the underlying gap (no second factor for maintainer) is real but lower-priority than initially scored, given the seed-maintainer CLI control. Re-classify from "High" to "Medium" in `findings.md` and add the condition above to the disposition.

### Phase 1.3 closeout

- ✅ Audit (a) complete: no web path to maintainer promotion. Stronger control than plan anticipated.
- ✅ Audit (b) complete: impact analysis written.
- ⏳ Audit (c) decision: **PENDING — user to confirm Option A** (with Gmail 2FA precondition) or pick B/C.

## Phase 1.4 — Brute-force / credential-stuffing posture

**Verdict:** ✅ (4 checks pass; no new findings; per-account lockout deemed unnecessary)

### Audit tests

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 1.4.1 | fail2ban filter regexes match the current nginx log format | ✅ | nginx log format (`deploy/kayak-log-format.conf`) starts with `$remote_addr - $remote_user [$time_local] "$request" $status …`. All 4 kayak filter regexes use `^<HOST> .* "(...)"` patterns that match the post-time-local portion. Verified per-filter:<br>• `nginx-edit-auth`: matches `^<HOST> .* "(?:GET\|POST) /edit\.php\b[^"]*" 401`. ✓<br>• `nginx-editor-auth`: matches `^<HOST> .* "(?:GET\|POST) /(?:login\|auth)\.php\b[^"]*" (?:40[0-9]\|429)` AND `^<HOST> .* "POST /login\.php\b[^"]*" 200` (catches drip-feed mailbombing). ✓<br>• `nginx-default-block`, `nginx-malicious`: per-pattern, all use compatible `^<HOST>` anchor. ✓ |
| 1.4.2 | Jail logpaths match where nginx writes | ✅ | `kayak-edit.conf` → `/var/log/nginx/kayak-access.log` matches `deploy/levels:329`. `kayak-editor-auth.conf` → same. `jail.local`'s `nginx-http-auth`, `nginx-limit-req` → `/var/log/nginx/kayak-error.log` matches `deploy/levels:330`. `nginx-malicious` → both default `/var/log/nginx/access.log` AND `/var/log/nginx/kayak-access.log`. `nginx-default-block` → `/var/log/nginx/blocked-access.log` (presumed; needs prod-confirm — see open item below). |
| 1.4.3 | Per-IP-only at nginx layer with botnet escalation path | ✅ | All `limit_req_zone` use `$binary_remote_addr`. A botnet rotating IPs gets per-IP throughput per IP, but: (a) nginx logs each limit-req trip to error log; (b) `nginx-limit-req` fail2ban jail bans IPs that trip the limit 5 times in 10 min; (c) `bantime.increment = true` (1h → 1d → 1w) makes repeat offenders progressively expensive. Adequate for the threat model. |
| 1.4.4 | Per-account lockout — needed? | ✅ (decided NO) | The realistic per-account brute-force scenario is mailbombing (capped: 5/email/hr by `magic_link_under_throttle`) or token-guess (256-bit hex; computationally infeasible). Session-cookie brute force is moot — sessions are sha256-hashed in DB; guessing a valid cookie requires guessing 256 bits. Conclusion: per-account lockout adds no defense against the actual attack surface; reject the option. |

### Findings

None new. The 4 brute-force defense layers (`nginx limit_req`, fail2ban, Turnstile on login/contact, application-side `magic_link_under_throttle`) are coherent and complete.

### Open prod-confirm

- `nginx-default-block` jail expects log at `/var/log/nginx/blocked-access.log`. The default-IP server block in `deploy/levels` (if present) needs to write to that path; otherwise the jail watches a non-existent file. Tier 6 prod-confirm.

### Phase 1.4 closeout

- ✅ All 4 audit tests pass. No new findings.
- One open prod-confirm item (default-block log path).
