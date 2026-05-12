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

> Convention: sections are organized by **original triage** priority, not current severity. An Accepted item stays under its original section (F-4/F-5/F-6/F-7 under High) so the historical risk surface is visible at a glance. The one exception is F-9, whose severity was unconditionally downgraded after audit refinement (Medium → Low) — moved under Low. F-13 keeps its Medium bucket because severity is conditional (Low at single-maintainer scale, Medium when a second maintainer joins).

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

- **Status:** ⚪ Accepted (per D-T2.4, 2026-05-12; see `decisions.md`)
- **Threats:** T-T2, T-R2
- **Severity:** High impact if exploited (silent rewrite of who-changed-what), low likelihood (requires SQL access).
- **Description:** `edit_history` is plain CRUD. No `previous_hash`, no append-only journal, no external sink. Anyone with DB write access (the operator, or a maintainer who's compromised the operator's shell, or a leaked DB backup with write paths) can `DELETE`/`UPDATE` rows without trace.
- **Remediation options:**
  - **None** (accept). DB-level access trusts the operator.
  - **Append-only journal**: write each insert also to `~/logs/edit_audit.log`, owned by a different user, no PHP write path. Simple, partial.
  - **External sink**: ship rows to a cheap S3-compatible bucket in append-only mode. Real protection; ~$1/month.
- **Plan tier:** Tier 2 decision point.

#### F-5 — Maintainer authentication is magic-link only (no 2FA)

- **Status:** ⚪ Accepted (per D-T1.3, 2026-05-12; see `decisions.md`)
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

- **Status:** ⚪ Accepted (per Phase 3.1 audit, 2026-05-12) — documented convention is adequate.
- **Threats:** T-T1 (stored XSS)
- **Severity:** ~~Medium-High impact~~ — no exploitable gap given the convention + actual codebase usage.
- **Description:** Original concern: "no calls specify `ENT_QUOTES | ENT_HTML5`." After Phase 3.1 audit:
  - `php/includes/html.php:6-13` documents the explicit convention: bare `htmlspecialchars($s)` everywhere; PHP 8.1+ defaults (`ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML401`) are correct for content + quoted-attribute contexts.
  - HTML5 unquoted-attribute interpolation is not present in the codebase (grep verified — all `\$var` interpolations into HTML attributes are inside double quotes).
  - The original F-6 framing missed the html.php docstring.
- **Re-evaluation trigger:** if the codebase introduces unquoted-attribute interpolation, OR if a PHP version downgrade puts the default flags back to pre-8.1, revisit.
- **Plan tier:** Tier 3.1 (this audit).

#### F-7 — Mass-assignment whitelist confirmation in propose/review

- **Status:** ⚪ Accepted (per Phase 2.3 audit — confirmed safe via cross-file invariant; refactor tracked in F-8)
- **Threats:** T-T3, T-E1
- **Severity:** ~~Critical impact if a whitelist gap exists~~ — gap does not exist.
- **Description:** Phase 2.3 audit traced the data flow:
  - `propose.php:51-56` defines `$reach_fields` (tier-gated); the POST loop iterates ONLY this whitelist.
  - `propose.php` stores the result in `change_request.payload_json`.
  - `review.php:50-56` builds `$applied['reach']` from `array_keys($payload['reach'])` — KEY SET is constrained to what the proposer submitted, which was already tier-whitelisted.
  - `review_logic.php:101` concats `$f = ?` where `$f` comes from this constrained key set.

  Conclusion: the SQL concat in `review_logic.php:101` and `edit.php:117` IS safe given the upstream invariants. F-7 closes; the refactor recommendation moves under F-8 (which tracks the code-smell aspect separately).
- **Plan tier:** Tier 2.3 (this audit). Refactor: see F-8.

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

- **Status:** 🟢 Closed (Tier 6 fix; deploy/levels server block + SETUP.md § 10).
- **Threats:** Adjacent to T-S3 (cookie-theft via MITM on first HTTP)
- **Description:** `deploy/SETUP.md:395` showed the intended header (`Strict-Transport-Security "max-age=63072000; includeSubDomains"`) marked "uncomment when SSL working." It was not present in `deploy/levels`.
- **Resolution:** Added `add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;` at server scope in `deploy/levels` (right after the security-headers snippet include). SETUP.md § 10 updated to verify with `curl -sI` and to clarify the snippet-vs-server-scope trade-off. `preload` qualifier intentionally OFF — that's a one-way commitment best left to a future explicit decision.
- **Verification:** After deploying, `curl -sI https://levels.wkcc.org/ | grep -i strict-transport` should show the header.
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

#### F-13 — No self-approval prevention in review.php

- **Status:** 🔴 Open (low priority — tied to multi-maintainer trigger)
- **Threats:** T-E6
- **Severity:** Low impact at single-maintainer scale (moot — maintainer could direct-edit anyway); Medium impact at multi-maintainer scale.
- **Description:** `review_approve()` in `php/includes/review_logic.php:61` takes `$cr, $applied, $maint_id` and does not check `$cr['editor_id'] !== $maint_id`. Realistic scenario: an editor with pending proposals gets promoted to maintainer; they can now approve their own pre-promotion proposals. (After promotion, `propose.php` routes them to `/edit.php`, so they cannot submit NEW proposals as maintainer.)
- **Phase 2.3 audit note:** at the current single-maintainer scale this is largely moot — the maintainer could direct-edit via `/edit.php` and achieve the same outcome. Becomes meaningful only when a second maintainer joins and you want to enforce "second pair of eyes."
- **Remediation:** One line in `review_approve()`: `if ($cr['editor_id'] === $maint_id) return ['ok' => false, 'err' => 'Cannot approve own proposal'];` (or downgrade to a require-other-maintainer flow if there are multiple maintainers).
- **Plan tier:** Tier 2.3 (this audit). Disposition: defer to multi-maintainer trigger (same trigger as F-5, D-T1.3).

### Low / prod-side confirms

This section holds two kinds of items: (a) findings downgraded to Low after audit refinement, and (b) verification steps that need prod-side access (the repo can't confirm them alone).

#### F-9 — Over-tier apply (review maintainer can write fields outside proposer's tier)

- **Status:** 🔴 Open (refined; severity downgraded)
- **Threats:** T-T6, T-E7
- **Severity:** **Low** (downgraded from Medium after Phase 2.3 audit).
- **Description:** Phase 2.3 audit refined the scope:
  - **Reach fields:** NOT vulnerable. `review.php:50-56` builds `$applied['reach']` from `array_keys($payload['reach'])` — keys are constrained to what the proposer submitted (which was tier-whitelisted). Maintainer cannot add `latitude_start` to the apply when the proposer only submitted `description`.
  - **reach_class:** Still vulnerable. `$applied['reach_class']` is built from POST `classes_present`/`classes`/`flow_low`/etc. (`php/review.php:58-74`), independent of `$payload`. Maintainer can add class changes the proposer didn't propose.
  - Mitigation: the `edit_history` row records `changed_by='maintainer:<id>'` with `change_request_id` linkage. Audit trail is technically correct — "this class change was applied by maintainer X during review of proposal Y." A reader can determine the maintainer ADDED the class change (not in payload_json).
- **Remediation options:** Same as before, but only for `reach_class`. The reach-fields concern resolves to non-issue.
  - Restrict `reach_class` apply to "only if `$payload['reach_class']` was set" — i.e., honor the proposer's intent.
  - OR add a UI flag on the review form: "I added these class changes" vs "proposer suggested these class changes" so the audit trail is explicit.
  - OR accept (current) — audit trail attribution is correct, just requires reading two columns to disambiguate.
- **Plan tier:** Tier 2 decision point (audit trail strength).

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

- **Status:** 🟢 Closed (Tier 6 fix; php/auth.php).
- **Threats:** T-S1, T-I4
- **Severity:** Medium impact (same vector as F-2), low-Medium likelihood (Referer-leak is universal for URLs-with-secrets).
- **Description:** After POST-consume of the magic-link, browser follows the 302 redirect. The Referer header on the follow-up request is the previous URL — `/auth.php?t=TOKEN&next=…`. Subsequent same-origin asset requests also carry this Referer until the user navigates away. nginx log format captures `$http_referer` (`deploy/kayak-log-format.conf`), so the token would be captured a SECOND time across all post-consume requests, even if F-2's `$request`-side redaction is in place.
- **Resolution:** Added `header('Referrer-Policy: no-referrer');` to `php/auth.php` immediately after the existing `header('Cache-Control: no-store');`. Applies to both GET (interstitial render) and POST (consume + 302). Browser respects the header on the redirect chain, so subsequent same-origin asset requests carry no Referer for the auth.php-originated navigation.
- **Verification:** After deploy, complete a login, then `tail /var/log/nginx/kayak-access.log`; the requests immediately following the auth.php POST should show `-` in the Referer column.
- **Plan tier:** Tier 1.1 (this finding). Effort: ~15min.

#### F-15 — No automated regression test for logout → session-replay → 401

- **Status:** 🟢 Closed (Tier 6 fix; tests/php/SessionRevocationTest.php + auth.php refactor).
- **Threats:** T-S4 (indirectly — defends against regression of the revoked_at filter)
- **Severity:** Low; informational.
- **Description:** `tests/php/` had no test covering the login → logout → replay → 401 flow. The bootstrap.php harness didn't even create the `editor_session` table. A future refactor that dropped `revoked_at`, `expires_at`, or `status != 'banned'` filters from `current_editor()` would not have broken any test.
- **Resolution:**
  1. Refactored `current_editor()` and `clear_editor_session()` in `php/includes/auth.php` to accept an optional `?PDO $db_override` parameter (idiomatic with the existing AuthTest pattern). Production callers pass nothing → same behavior; tests inject an in-memory PDO. The per-request static cache is bypassed when an override is passed.
  2. Extended `tests/php/bootstrap.php`'s `kayak_test_pdo()` to include the full `editor_session` schema.
  3. Added `tests/php/SessionRevocationTest.php` with 6 cases:
     - live session resolves to the editor;
     - revoked session (via `clear_editor_session`) replays to null;
     - expired session returns null;
     - banned editor with live session returns null;
     - missing cookie returns null;
     - malformed cookie (non-hex) returns null without DB access.
- **Verification:** `make test-php` (or `./vendor/bin/phpunit tests/php/SessionRevocationTest.php`) — exercises all 6 cases.
- **Plan tier:** Tier 1.2 (this finding). Test addition is Tier 6 (apply findings).

#### F-16 — Privacy policy "Your Rights" section contradicts the rest of the page

- **Status:** 🟢 Closed (Tier 6 fix; php/privacy.php).
- **Threats:** Not a code-side threat; trust/policy correctness.
- **Severity:** Low (user-trust-facing). The page read as self-contradicting and signaled carelessness on a page whose primary purpose is to project care.
- **Description:** `php/privacy.php` "Your Rights" section read: *"Because we collect only server access logs and no personal data, there is generally no personal data to access, correct, or delete."* This contradicted the upper "Data We Collect" section which lists contributor email addresses, proposed edits, comments, and cookies. The page was likely written before the editor pipeline existed and the "Your Rights" section was not refreshed when editor + change_request + edit_history landed.
- **Resolution:** Rewrote the "Your Rights" section to accurately describe deletion (D-T4.1), export (D-T4.2), audit-trail retention (D-T4.3a), and cookie lifecycle (D-T4.3c). Bumped "Last updated" to 2026-05-12. Added HTML comment `<!-- Annual review trigger: next review 2027-05-12 -->` above the prose block.
- **Verification:** Visit `/privacy.php` after deploy; confirm the new section text and updated date are visible.
- **Plan tier:** Tier 4.4 (this finding). Implementation in Tier 6. Effort: ~20 min.

## Findings by status

| Status | Count | IDs |
|---|---|---|
| 🔴 Open | 8 | F-2, F-3, F-8, F-9, F-10, F-11, F-12, F-13 |
| 🟡 In progress | 0 | — |
| 🟢 Closed | 4 | F-1, F-14, F-15, F-16 (Tier 6) |
| ⚪ Accepted | 4 | F-4 (per D-T2.4), F-5 (per D-T1.3), F-6 (Phase 3.1 — documented convention adequate), F-7 (Phase 2.3 — confirmed safe) |
| 🔵 Deferred | 0 | — |

## Per-tier work allocation

- **Tier 1** (auth review) — F-2, F-5, F-1, F-3, plus the gap audits that feed Tier 1's verification gate.
- **Tier 2** (authz review) — F-7, F-8, F-9, F-13. Decision point on F-4.
- **Tier 3** (I/O) — F-6, plus full XSS sweep that may add findings.
- **Tier 4** (user-data) — produces D-T4.1..5 decisions; new F-16 (privacy policy "Your Rights" stale); Tier 6 implements `levels delete-editor`, `levels export-editor`, `levels editor-retention` CLIs + privacy.php refresh.
- **Tier 5** (disclosure / response) — out-of-band; mostly decisions.
- **Tier 6** (closeout) — apply remaining open findings; document accepted ones.

## Maintenance

This doc is the single source of truth for security findings. Update it when:
- A new finding surfaces during any tier's work
- A finding's status changes
- A finding is closed (note the commit hash)
