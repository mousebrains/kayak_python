# Tier 2 — Authorization review audit log

> **Started:** 2026-05-12 against `main` at `aadb63c`. Per `docs/PLAN_editor_security_review.md` Tier 2 verification gate: "Authorization matrix documented (role × endpoint → expected response); each row tested; IDOR sweep produced no findings (or each one is filed)."
>
> Verdict legend: ✅ pass / ⚠ partial / ❌ fail / ⊘ N/A.

## Phase 2.1 — Role enforcement audit

**Verdict:** ✅ (consistent helper-based pattern; no findings)

### Authorization matrix

For each of the 10 editor-pipeline endpoints, what's the expected behavior at each authentication tier?

| Endpoint | Unauth (no cookie) | Editor (status≠maintainer, not banned) | Maintainer (status=maintainer) | Banned editor |
|---|---|---|---|---|
| `/account.php` | 302 → /login.php?next=/account.php | 200 (own row only) | 200 | 302 → /login.php (current_editor returns null for banned) |
| `/admin.php` | 302 → /login.php | 403 ("only available to maintainer") | 200 | 302 → /login.php |
| `/auth.php` | 200 if token valid; 400 if expired/invalid (no session required) | same | same | same — token consumption creates a new session if editor not banned |
| `/comment.php` | 302 → /login.php | 200 (POST creates change_request) | 200 | 302 → /login.php |
| `/contact.php` | 200 (intentionally open) | 200 | 200 | 200 |
| `/edit.php` | 302 → /login.php | 403 | 200 (full reach/gauge edit) | 302 → /login.php |
| `/login.php` | 200 (form) | redirect to /account.php (per `php/login.php` "redirects if already logged in" note) | redirect to /account.php | not applicable — banned editor cannot log in (issue_magic_link short-circuits) |
| `/logout.php` | 200 (idle form; no-op POST) | 200 → revokes session → 302 / | 200 → revokes session → 302 / | 200 (no session to revoke) |
| `/propose.php` | 302 → /login.php | 200 (tier-gated fields) | 302 → /edit.php (maintainers bounced) | 302 → /login.php |
| `/review.php` | 302 → /login.php | 403 | 200 | 302 → /login.php |

### Audit tests

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 2.1.1 | Role checks are present | ✅ | `require_editor()` or `require_maintainer()` called at top of every gated endpoint:<br>• `account.php:16`, `admin.php:17`, `comment.php:19`, `edit.php:28`, `propose.php:21`, `review.php:19`<br>Feature gate via `require_editor_feature()`: `auth.php:20`, `login.php:18`, `logout.php:14`. |
| 2.1.2 | Role checks use a consistent helper (not ad-hoc) | ✅ | All 6 require-* calls use the helper functions from `php/includes/auth.php`. No file checks `current_editor()` directly when access is required-and-fail-out (the proper helper combines fetch + redirect/403). |
| 2.1.3 | Role checks precede data access | ✅ | In every endpoint, `require_X()` is called BEFORE any DB read or output. Verified line-by-line in Phase 0.1 inventory + spot-checks here. Order is consistent: feature-gate → role-check → CSRF-check (POST only) → handler body. |
| 2.1.4 | Banned-editor enforcement | ✅ | `current_editor()` SQL filter `e.status != 'banned'` (`php/includes/auth.php:current_editor`). Banned editors are indistinguishable from unauth in `require_editor()` — they get 302'd to login. login.php then short-circuits on banned in `issue_magic_link()` ("banned" → returns `editor_id => 0` without sending an email; UX is "same response as new editor" so no enumeration leak). |
| 2.1.5 | Maintainer-only enforcement | ✅ | `require_maintainer()` chains `require_editor()` then checks `is_maintainer($ed)`. On failure: 403 page with the user's email + status (informational only). Cannot be bypassed without already holding a session cookie + the editor row being status='maintainer'. |
| 2.1.6 | Response code semantics | ⚠ (note, not finding) | Plan's "401/200/200" simplification: actual response is **302 → /login.php** for unauth (not 401). For browser-facing endpoints this is correct UX; for non-browser clients it's unconventional but acceptable. No machine-consumed editor endpoints (json/api) exist in the editor pipeline; `api.php`/`data.php`/`latest.php` are out of scope for this tier. |

### Notes

- The `auth.php` token-only flow doesn't follow the require-X pattern — it's gated by the token (peek/consume) instead. The token IS the credential here; the resulting session is created server-side by `set_editor_session()` post-consume.
- `current_editor()`'s SQL filter combines four conditions (`token_hash`, `revoked_at IS NULL`, `expires_at > now`, `status != 'banned'`). Any single condition failure → null → `require_editor()` redirects. Strong single-point-of-truth pattern.
- `is_maintainer()` is a string comparison on `$ed['status']`. Cannot be forged client-side because `$ed` comes from `current_editor()` which reads from the DB.

### Phase 2.1 closeout

- ✅ Authorization matrix documented; all 6 audit tests pass (1 with a non-finding note about response-code semantics).
- No new findings; existing F-7/F-8/F-13 carry forward to 2.3.
