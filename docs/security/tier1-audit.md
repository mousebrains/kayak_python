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
