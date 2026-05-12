# Editor pipeline — security findings

> **Status:** Seeded at Tier 0 (2026-05-12). 13 findings filed; each gets a Tier-1+ disposition (fix / accept-as-risk / defer-with-date).
>
> Cross-references: threats in [threat-model.md](threat-model.md) (`T-Xn`); controls in [controls-map.md](controls-map.md). See `docs/PLAN_editor_security_review.md` for the tier workflow.

## Status legend

- 🔴 **Open** — known gap, no disposition yet
- 🟡 **In progress** — fix or decision in flight
- 🟢 **Closed/Fixed** — control added, change merged
- ⚪ **Accepted** — explicitly accepted as risk with rationale
- 🔵 **Deferred** — scheduled for a later date with explicit trigger

## Findings by priority

### High

#### F-2 — Magic-link token captured in nginx access log

- **Status:** 🔴 Open
- **Threats:** T-S1, T-I4
- **Severity:** Medium impact, High likelihood for someone with log access (anyone with read on `/var/log/nginx/`)
- **Description:** `/auth.php?t=<64-hex-token>` is the magic-link consumption URL. nginx access log captures full `$request` (`deploy/levels:329`), so the token lands in `/var/log/nginx/kayak-access.log`. Single-use + 30-min expiry mitigate: a leaked log token is dead within minutes of legitimate consumption, and unconsumed tokens have a 30-min exposure window.
- **Repro:** `tail /var/log/nginx/kayak-access.log` shortly after a login → see `/auth.php?t=<token>` in the request column.
- **Remediation options:**
  - Redact `t=` param in the log format: in `deploy/kayak-log-format.conf`, set `$clean_request` via `map` directive that rewrites `/auth.php?t=…` to `/auth.php?t=REDACTED`.
  - OR change `auth.php` to accept the token via POST (form auto-submit interstitial), which keeps it out of GET URLs entirely. The existing GET/POST split (peek vs consume) already half-does this; pushing all token traffic to POST closes the gap.
  - OR shorten log retention aggressively (1-2 days).
- **Plan tier:** Tier 1.1 (audit-and-decide).

#### F-4 — `edit_history` has no tamper-resistance

- **Status:** 🔴 Open
- **Threats:** T-T2, T-R2
- **Severity:** High impact if exploited (silent rewrite of who-changed-what), low likelihood (requires SQL access).
- **Description:** `edit_history` is plain CRUD. No `previous_hash`, no append-only journal, no external sink. Anyone with DB write access (the operator, or a maintainer who's compromised the operator's shell, or a leaked DB backup with write paths) can `DELETE`/`UPDATE` rows without trace.
- **Remediation options:**
  - **None** (accept). DB-level access trusts the operator.
  - **Append-only journal**: write each insert also to `~/logs/edit_audit.log`, owned by a different user, no PHP write path. Simple, partial.
  - **External sink**: ship rows to a cheap S3-compatible bucket in append-only mode. Real protection; ~$1/month.
- **Plan tier:** Tier 2 decision point.

#### F-5 — Maintainer authentication is magic-link only (no 2FA)

- **Status:** 🔴 Open (pending Tier 1.3 decision; see `tier1-audit.md`)
- **Threats:** T-S2 (specifically for maintainer accounts)
- **Severity:** **Medium** (downgraded from High after Phase 1.3 audit — see below).
- **Description:** Maintainers use the same `/login.php` magic-link flow as editors. Email-account compromise = full maintainer-account takeover. The `maintainer_credential` schema is provisioned for WebAuthn but no PHP endpoints implement registration/assertion.
- **Phase 1.3 audit refinement:** the threat model is materially better than initially scored because there is **no web path to maintainer promotion** — the only way to set `editor.status = 'maintainer'` is the CLI `levels seed-maintainer`. So a web-side maintainer takeover cannot create *additional* maintainer accounts; the compromise stays bounded to the one account whose email was compromised. The `admin.php:ban` action even guards `status != 'maintainer'`, so a compromised maintainer cannot demote other maintainers via web.
- **Decision menu (Tier 1.3 decision point):**
  - **A. Magic-link only (current).** Relies on maintainer's Gmail having strong 2FA. ~$0 cost. Recommended for the current single-maintainer posture.
  - **B. Advance Phase 1b WebAuthn.** ~1-2 days. Phishing-resistant. Right answer if a second maintainer joins.
  - **C. TOTP fallback.** ~1 day. Phishable; less work than WebAuthn but lower security. Dominated by B.
- **Recommendation:** Option A, conditional on (1) maintainer's Gmail has 2FA enabled, (2) re-evaluate when adding a second maintainer, (3) re-evaluate if any audit-trail integrity work (F-4) happens (strong audit + weak auth is inconsistent).
- **Plan tier:** Tier 1.3 (decision); Tier 6 if a future trigger demotes the answer to Option B.

#### F-6 — `htmlspecialchars` calls don't specify ENT flags

- **Status:** 🔴 Open
- **Threats:** T-T1 (stored XSS)
- **Severity:** Medium-High impact, low-Medium likelihood (defaults are mostly safe).
- **Description:** Grep across `php/*.php`: zero calls use explicit `ENT_QUOTES | ENT_HTML5`. PHP 8.1+ defaults to `ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML401`, which is OK for content contexts and quoted attribute contexts, but inadequate for HTML5 unquoted-attribute contexts (rare in this codebase but possible).
- **Remediation:** Define a project-local `escape()` helper in `php/includes/html.php` (or wherever a render helper lives) that wraps `htmlspecialchars($s, ENT_QUOTES | ENT_HTML5, 'UTF-8')` and grep-replace call sites. Audit each call's context (content vs attribute) in the same pass.
- **Plan tier:** Tier 3.1 (XSS sweep). Also a candidate for the PHP-layer-split plan as a "cross-file convention" rule.

#### F-7 — Mass-assignment whitelist confirmation in propose/review

- **Status:** 🔴 Open
- **Threats:** T-T3, T-E1
- **Severity:** Critical impact if a whitelist gap exists, low likelihood given the surrounding code.
- **Description:** `account.php` and `edit.php` have clear whitelist patterns. `propose.php` is tier-gated but the per-tier field whitelist needs explicit verification. `review.php` constructs `$applied['reach']` from `$_POST` (via `review_logic.php`) — the keys must be whitelisted upstream of the `UPDATE $table SET ...` concat. Code is probably correct (only maintainers can hit review.php) but the pattern is fragile.
- **Remediation:** Add a `$ALLOWED_REACH_FIELDS` / `$ALLOWED_GAUGE_FIELDS` const at the top of `propose.php` and `review_logic.php`; assert every applied key is in the const. Move the consts to `php/includes/auth.php` or a new `php/includes/schema.php` so they're share-able.
- **Plan tier:** Tier 2.1 / Tier 2.3.

#### F-8 — `UPDATE $table SET $sets` SQL string concat in edit.php + review_logic.php

- **Status:** 🔴 Open
- **Threats:** T-T4, T-E2
- **Severity:** Critical impact if a column or table name from user input ever lands in the concat; low likelihood with current callers.
- **Description:** Two sites concat into `prepare()`:
  - `php/edit.php:117` — `prepare('UPDATE ' . $table . ' SET ' . implode(', ', $sets) . ' WHERE id = ?')`
  - `php/includes/review_logic.php:101` — same pattern
  
  Both currently use whitelisted `$table` and `$sets` (the `$field = ?` strings have field names from the editable-field list). Safe in current usage; the pattern is a code smell — a future contributor could pass user-supplied keys.
- **Remediation:** Refactor to a 2-element dispatch table (`reach` / `gauge`) with const column lists, and a helper that builds the `SET` clause from a const-whitelisted dict. Pair with F-7.
- **Plan tier:** Tier 2.1 / Tier 2.3.

### Medium

#### F-1 — HSTS not enabled

- **Status:** 🔴 Open
- **Threats:** Adjacent to T-S3 (cookie-theft via MITM on first HTTP)
- **Description:** `deploy/SETUP.md:395` shows the intended header (`Strict-Transport-Security "max-age=63072000; includeSubDomains"`) marked "uncomment when SSL working." Not present in `deploy/levels`. Prod-side `sudo nginx -T | grep Strict-Transport-Security` would confirm whether a snippet on the host overrides this.
- **Remediation:** Add the directive in `deploy/levels` and a nginx snippet on the host (`/etc/nginx/snippets/security-headers.conf`). Add `preload` qualifier and submit to hstspreload.org if going long-term.
- **Plan tier:** Tier 1.2 (session audit) / Tier 6 (apply).

#### F-3 — Email-alias normalization

- **Status:** 🔴 Open
- **Threats:** T-S6, T-D2
- **Description:** `normalize_email()` in `php/includes/auth.php` is `strtolower(trim(...))`. Gmail's `Foo.Bar+test@gmail.com` and `foobar@gmail.com` resolve to *different* `editor` rows. An attacker spawns N alias accounts to: (1) bypass per-account daily caps; (2) dilute audit trail; (3) sock-puppet proposal volume.
- **Remediation options:**
  - Detect Gmail/Google Workspace domains; strip `.` from local-part; strip `+tag`. Other providers don't have the same alias semantics.
  - OR enforce one-account-per-canonical-email globally with a more aggressive normalization.
  - OR accept as low-impact (paddler audit isn't a high-stakes audit context).
- **Plan tier:** Tier 1.5 (account-recovery audit) decision point.

#### F-9 — Over-tier apply (review maintainer can write fields outside proposer's tier)

- **Status:** 🔴 Open
- **Threats:** T-T6, T-E7
- **Description:** `review.php` lets the maintainer edit `$applied['reach']` before approving. A tier-`minimal` proposer can only edit description+features in `propose.php`, but the maintainer can add lat/lon/classes to the applied payload. The `edit_history` rows record the change but attribute it to `maintainer:<id>`, not `editor:<id>` — so audit trail is technically correct. The concern is the proposer-history clarity: "did the proposer suggest those coordinates or did the maintainer?"
- **Remediation options:**
  - Add `applied_by` column to `change_request` separately from `reviewed_by` — N/A (same thing).
  - Render a "maintainer-tweaked" annotation in `edit_history.new_value` whenever the value differs from the proposed payload.
  - Restrict the review-form fields to the proposer's tier; force maintainer to direct-edit if more is needed.
- **Plan tier:** Tier 2 decision point.

#### F-13 — No self-approval prevention in review.php

- **Status:** 🔴 Open
- **Threats:** T-E6
- **Severity:** Medium impact, very low likelihood.
- **Description:** `review.php` doesn't check `change_request.editor_id !== $maint['id']`. The realistic scenario: an editor with pending proposals gets promoted to maintainer; they can now approve their own pre-promotion proposals. (After promotion, `propose.php` routes them to `/edit.php`, so they can't submit new proposals as a maintainer.)
- **Remediation:** One line in `review_approve()`: assert `$cr['editor_id'] !== $maint_id` (or downgrade to a require-other-maintainer flow if there are multiple maintainers).
- **Plan tier:** Tier 2.3 (privilege escalation paths).

### Low / prod-side confirms

These are not gaps per se — they're verification steps that need prod-side access (the repo can't confirm them alone).

#### F-10 — Confirm `display_errors=Off` and `expose_php=Off` in prod php.ini

- **Status:** 🔴 Open (prod-confirm)
- **Threats:** T-I5
- **Repro:** `php-fpm -i 2>&1 | grep -E 'display_errors|expose_php'` or via `phpinfo()` audit (locally, not in prod).

#### F-11 — Confirm nginx error log file permissions

- **Status:** 🔴 Open (prod-confirm)
- **Threats:** T-I6
- **Repro:** `stat /var/log/nginx/kayak-error.log` on prod; expect `nginx:adm 0640` or similar.

#### F-12 — Confirm PHP-FPM timeout + worker count

- **Status:** 🔴 Open (prod-confirm)
- **Threats:** T-D6
- **Repro:** Read the prod PHP-FPM pool config: `cat /etc/php/*/fpm/pool.d/*.conf | grep -E 'request_terminate_timeout|pm.max_children|pm.start_servers'`. Expect `request_terminate_timeout` set to a non-zero value (e.g. 30s) to bound slow-loris workers.

#### F-14 — Magic-link token leaks via Referer to subsequent requests

- **Status:** 🔴 Open
- **Threats:** T-S1, T-I4
- **Severity:** Medium impact (same vector as F-2), low-Medium likelihood (Referer-leak is universal for URLs-with-secrets).
- **Description:** After POST-consume of the magic-link, browser follows the 302 redirect. The Referer header on the follow-up request is the previous URL — `/auth.php?t=TOKEN&next=…`. Subsequent same-origin asset requests (`/static/leaflet.js`, `/static/style*.css`, etc.) also carry this Referer until the user navigates away. nginx log format captures `$http_referer` (`deploy/kayak-log-format.conf`), so the token is captured a SECOND time across all post-consume requests, even if F-2's `$request`-side redaction is in place.
- **Repro:** `tail /var/log/nginx/kayak-access.log` after a login; look at the Referer column on the requests immediately after the POST-consume. Expect `/auth.php?t=<token>&next=…` in each Referer.
- **Remediation:** Add `header('Referrer-Policy: no-referrer')` to `php/auth.php` (the response that initiates the navigation away). Ideally also to `set_editor_session()`'s caller context so the policy survives the redirect chain.
  - Why `no-referrer` (not `same-origin` or `strict-origin`): `same-origin` still sends the full Referer to same-origin requests (the exact bad case here). `strict-origin` trims to origin only — adequate but slightly weaker than `no-referrer` for the immediate-post-consume window.
  - Marginal alternative: add `<meta name="referrer" content="no-referrer">` to the auth.php HTML. Header is preferred (covers non-HTML responses like the 302).
- **Plan tier:** Tier 1.1 (this finding). Effort: ~15min.

#### F-15 — No automated regression test for logout → session-replay → 401

- **Status:** 🔴 Open (test coverage, not vulnerability)
- **Threats:** T-S4 (indirectly — defends against regression of the revoked_at filter)
- **Severity:** Low; informational. Static analysis confirms `current_editor()` filters `s.revoked_at IS NULL`, so logout immediately revokes the session token; replaying the cookie returns null and downstream `require_editor()` redirects to /login. No live vulnerability today.
- **Description:** `tests/php/` has no test covering the login → capture-cookie → logout → replay → 401 flow. The bootstrap.php test harness doesn't even create the `editor_session` table (`kayak_test_pdo()` only seeds `editor` + `editor_magic_link`). A future refactor that drops the `revoked_at` SQL clause from `current_editor()` would not break any test.
- **Repro:** `grep -nE "logout|revoke|revoked" tests/php/*.php` → no matches.
- **Remediation options:**
  - Extend `tests/php/bootstrap.php` `kayak_test_pdo()` to include the `editor_session` schema. Add a new `tests/php/SessionRevocationTest.php` with two cases: (a) `current_editor()` returns the editor when session is live; (b) returns null after `clear_editor_session()`. ~30 min.
  - OR a once-only live manual test on staging, recorded in `tier1-audit.md`. ~5 min, no regression protection.
- **Plan tier:** Tier 1.2 (this finding). Test addition is Tier 6 (apply findings).

## Findings by status

| Status | Count | IDs |
|---|---|---|
| 🔴 Open | 15 | F-1 through F-15 |
| 🟡 In progress | 0 | — |
| 🟢 Closed | 0 | — |
| ⚪ Accepted | 0 | — |
| 🔵 Deferred | 0 | — |

## Per-tier work allocation

- **Tier 1** (auth review) — F-2, F-5, F-1, F-3, plus the gap audits that feed Tier 1's verification gate.
- **Tier 2** (authz review) — F-7, F-8, F-9, F-13. Decision point on F-4.
- **Tier 3** (I/O) — F-6, plus full XSS sweep that may add findings.
- **Tier 4** (user-data) — decision-rich tier; doesn't directly act on the open findings but produces account-deletion / data-export choices that interact with F-3 (alias).
- **Tier 5** (disclosure / response) — out-of-band; mostly decisions.
- **Tier 6** (closeout) — apply remaining open findings; document accepted ones.

## Maintenance

This doc is the single source of truth for security findings. Update it when:
- A new finding surfaces during any tier's work
- A finding's status changes
- A finding is closed (note the commit hash)
