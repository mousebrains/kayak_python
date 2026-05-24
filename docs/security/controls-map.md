# Existing-controls map

> **Drafted:** 2026-05-12 against `main` at `beaf58a`. Part of `docs/done/PLAN_editor_security_review.md` Tier 0.4. Each threat from [threat-model.md](threat-model.md) gets one row: existing control (with `file:line`), status (✓ in place / ⚠ partial / ✗ missing), and a finding marker `[F-Nn]` linking to [findings.md](findings.md) where status is ⚠/✗.
>
> Read [editor-surface.md](editor-surface.md) for the asset/component reference; [threat-model.md](threat-model.md) for the threat IDs (`T-Sn`/`T-Tn`/`T-Rn`/`T-In`/`T-Dn`/`T-En`).

## Verification approach

For each threat, this map answers: *what code prevents or detects this?* It does NOT yet test exploitability — that's Tier 1.5/2.2/3.1 in-tier work. A ✓ here means "the control exists and looks structurally correct"; an exploit test in a later tier could still demote it to ⚠.

Format:
- ✓ — control exists, structurally sound, no immediate concern.
- ⚠ — control exists but has a known weakness or gap.
- ✗ — no control; relies on the threat being out of reach for other reasons (or a real gap).
- ⊘ — N/A (threat doesn't apply with current implementation).

## S — Spoofing

| Threat | Control | Status | Notes |
|---|---|---|---|
| T-S1 | Magic-link single-use + 30-min expiry: `consume_magic_link()` sets `used_at` in a transaction (`php/includes/auth.php:consume_magic_link`); token hashed at rest (`token_hash = sha256(tok)`). | ⚠ **[F-2]** | Raw token still hits nginx access log via `$request` (`conf/sites/levels-wkcc-org`); newly-issued tokens are exploitable for the window between email-send and user-click. |
| T-S2 | None code-side. Defended by user-managed Gmail account 2FA. | ✗ **[F-5]** | Specifically for **maintainer** accounts: same vector, much higher impact. `maintainer_credential` schema present but unwired. |
| T-S3 | Cookie `HttpOnly` via `_cookie_params()` (`php/includes/auth.php:_cookie_params`). CSP enforced via nginx snippet (prod-side, not in repo). | ⚠ | Depends on the CSP snippet's `script-src` strictness — confirm in Tier 1 prod check. HSTS not enabled in repo (`deploy/SETUP.md:395` shows "uncomment"; not in `deploy/levels`) — see **[F-1]**. |
| T-S4 | 7-day flat absolute expiry; logout server-side via `editor_session.revoked_at`; cookie is `Secure` + `SameSite=Strict`; logout-then-replay returns 401 (CHECK design — `current_editor()` query filters `revoked_at IS NULL`). | ✓ | No IP-binding on session, intentional (mobile/laptop roaming). 7-day window for stolen cookie is the dominant remaining exposure. |
| T-S5 | Double-submit cookie + `hash_equals` constant-time compare (`require_csrf()` in `php/includes/auth.php:require_csrf`); CSRF cookie rotated on session creation (`set_editor_session()`). Coverage: 10/10 editor POST handlers call `require_csrf()` (verified by grep). | ✓ | Strong. |
| T-S6 | `normalize_email()` only `strtolower(trim(...))` — does not strip Gmail dots/`+tags`. | ✗ **[F-3]** | Multiple accounts per human possible; daily caps per-`editor.id`. |
| T-S7 | nginx `limit_req` keys on `$binary_remote_addr` (`deploy/ratelimit.conf`). | ⚠ | If Cloudflare proxies, real IP needs `set_real_ip_from` + `X-Forwarded-For` chain. Audit gate to confirm CF/non-CF posture. |

## T — Tampering

| Threat | Control | Status | Notes |
|---|---|---|---|
| T-T1 | `htmlspecialchars()` used in 15/22 PHP files (verified by grep). All 10 editor entry points use it. | ⚠ **[F-6]** | NO calls specify `ENT_QUOTES \| ENT_HTML5` flags (zero matches in grep). PHP 8.1+ defaults to `ENT_QUOTES \| ENT_SUBSTITUTE \| ENT_HTML401` — adequate for most contexts but not HTML5-attribute-strict. Plan Phase 3.1 calls for explicit `ENT_QUOTES \| ENT_HTML5`. Per-call audit for context-correctness is Tier 3.1. |
| T-T2 | None. `edit_history` is plain CRUD, no hash chain, no append-only journal, no external sink. | ✗ **[F-4]** | Decision point in Tier 2: do nothing / append-only journal / external sink. |
| T-T3 | `account.php:22-29` whitelists `display_name` only (max 128 chars). `edit.php:47/64` defines `$editable_fields` (per type) and the POST loop only iterates that list (`php/edit.php:92-108`). `propose.php` tier-gates via `$tier = $ed['status']` (`php/propose.php:50`); needs per-tier whitelist confirmation in Tier 2.3. `review.php` constructs `$applied['reach']` from `$_POST` — the keys must be whitelisted upstream. | ⚠ **[F-7]** | account/edit are clean. propose/review need closer reading to confirm no mass-assignment vectors. |
| T-T4 | Repo convention is parameterized `prepare(...)->execute([...])`. Grep for `prepare("$|prepare(<concat>"` finds: `IN ($placeholders)` patterns (safe — placeholders constructed from `?, ?, ?` strings); `UPDATE $table SET $sets` patterns at `php/edit.php:117` and `php/includes/review_logic.php:101`. | ⚠ **[F-8]** | The `UPDATE $table SET $sets` concat is safe given current usage (`$table` ∈ `{reach, gauge}`; `$field` from `$editable_fields` whitelist), but the pattern is a code smell — a future contributor could pass un-whitelisted input. Refactor to const dispatch tables. |
| T-T5 | `tests/php/ReviewApproveRaceTest.php` covers concurrent-approval race. `review_logic.php` re-fetches `change_request.status = 'pending'` inside the transaction. | ✓ | Test-covered. |
| T-T6 | None. `review.php` allows maintainer to edit `$applied['reach']` before approving. | ⚠ **[F-9]** | Maintainer is trusted (they could direct-edit anyway), so the security risk is limited — but the audit-trail records what was applied, not what was originally proposed, blurring "who proposed what." Decision point in Tier 2. |

## R — Repudiation

| Threat | Control | Status | Notes |
|---|---|---|---|
| T-R1 | `change_request.editor_id` + `submitted_at` (`models.py:761`); nginx access log records request; msmtp log records send. | ✓ | Triangulates for honest dispute. |
| T-R2 | `edit_history.changed_by = 'maintainer:<id>' / 'editor:<id>'` + `changed_at` (`models.py:836`). | ⚠ **[F-4]** | Same root cause as T-T2 — operator-level rewrite leaves no trace. Repudiation by maintainer is detectable only by external corroboration. |
| T-R3 | `editor_magic_link.ip_issued` + `created_at` (`models.py:702`) + nginx access log. | ✓ | Adequate for low-stakes "I didn't request that link" disputes. |

## I — Information disclosure

| Threat | Control | Status | Notes |
|---|---|---|---|
| T-I1 | `review.php` calls `require_maintainer()` (`php/review.php:19`); `propose.php` filters `change_request` by `editor_id = current_editor` (verified in Phase 1 reading). | ✓ | Need Tier 2.2 IDOR sweep to confirm no GET/POST path leaks cross-editor data. |
| T-I2 | `admin.php` requires maintainer (`php/admin.php:17`); `account.php` shows only `$ed` (current). Maintainer notification emails (`comment.php`, `propose.php`) include proposer email → goes to operator inbox only. | ✓ | The proposer-email-in-email is by design (Reply-To). |
| T-I3 | Session cookie sha256-stored in DB. nginx access log captures `$request` not headers, so `Cookie:` doesn't leak. PHP-FPM logs do not include `$_COOKIE` (audit gate). | ✓ | Confirm `display_errors=Off` in prod php.ini (Tier 0.4 prod check). |
| T-I4 | Cross-listed with T-S1; same controls/gaps. | ⚠ **[F-2]** | Same. |
| T-I5 | `expose_php=Off` and `display_errors=Off` expected per `deploy/SETUP.md`. | ⚠ **[F-10]** | Confirm with prod-side `php -i \| grep display_errors`. Repo cannot verify alone. |
| T-I6 | nginx error log mode — convention is `nginx:adm 640`. | ⚠ **[F-11]** | Confirm with prod-side `stat /var/log/nginx/kayak-error.log`. |
| T-I7 | Hetzner storage-box backups + rclone-crypt offsite (`docs/offsite-backup.md`). | ✓ | Crypt key custody is the residual risk. Tracked in offsite-backup.md, not here. |
| T-I8 | No entry point renders `edit_history` (verified by grep across `php/`). | ✓ | If a maintainer audit view is added later, re-verify. |

## D — Denial of service

| Threat | Control | Status | Notes |
|---|---|---|---|
| T-D1 | `magic_link_under_throttle()` (5/email/hr, 20/IP/hr) + nginx `login:3r/min` + Turnstile on `/login.php`. | ✓ | Three layers. |
| T-D2 | `propose.php` tier daily caps (3/10/20); `comment.php` daily cap 5; compounded by [F-3] alias-multiplier. | ⚠ **[F-3]** | T-S6 + T-D2 are co-listed because alias fix mitigates both. |
| T-D3 | Review-list pagination + default `status=pending` filter (verified in `php/review.php` reading). | ✓ | UI scales. |
| T-D4 | nginx `global:20r/s + burst=40` per IP (`deploy/ratelimit.conf` + `conf/snippets/levels-common.conf`). | ⚠ | Per-IP only; distributed flood not addressed without CDN/WAF. |
| T-D5 | nginx truncates `$request` to ~8K by default. | ✓ | Adequate. |
| T-D6 | nginx `fastcgi_read_timeout` / `proxy_read_timeout` (need prod-side audit; default 60s). PHP-FPM worker count bounded by pool config. | ⚠ **[F-12]** | Confirm timeouts + worker count in prod-side `php-fpm` pool conf. |

## E — Elevation of privilege

| Threat | Control | Status | Notes |
|---|---|---|---|
| T-E1 | Same as T-T3. | ⚠ **[F-7]** | account/edit clean; propose/review per-field whitelist needs verify. |
| T-E2 | Same as T-T4. | ⚠ **[F-8]** | Refactor concat to const dispatch. |
| T-E3 | `maintainer_credential` schema unwired (verified: no PHP code references WebAuthn). | ⊘ | Phase 1.3 decision: wire or accept. |
| T-E4 | sha256-hashed session tokens; raw value only in `Set-Cookie` and client cookie store. `random_bytes(32)` CSPRNG. | ✓ | Cryptographic; can't be forged. |
| T-E5 | `safe_next_url()` rejects `^/[^/\\]` (`php/includes/auth.php:safe_next_url`) — `//evil.com`, `/\evil.com`, `///x`, `/\\x` all rejected. Same-origin `/path?…` accepted. | ✓ | Tight regex. Audit gate (`SanityTest.php`) covers it. |
| T-E6 | None. Review code does not check `change_request.editor_id !== $maint['id']`. | ⚠ **[F-13]** | Maintainer can in principle approve their own pre-promotion proposals. Low likelihood (new maintainer with backlog) but real gap. |
| T-E7 | Cross-listed with T-T6. | ⚠ **[F-9]** | |

## Findings summary

Twelve findings filed in [findings.md](findings.md), grouped by priority:

**Critical (per threat-model.md priority matrix):**
- None unguarded — all critical-bucket threats have at least partial controls. T-T3/T-T4 are ⚠ (whitelist-based defense exists but pattern is fragile); T-S2 is ✗ for maintainer accounts (only).

**High:**
- [F-2] Magic-link token in nginx access log
- [F-4] `edit_history` tamper-resistance
- [F-5] Maintainer 2FA absent
- [F-6] `htmlspecialchars` defaults — needs context audit
- [F-7] Mass-assignment whitelist confirmation in propose/review
- [F-8] `UPDATE $table SET $sets` concat code smell

**Medium:**
- [F-1] HSTS not enabled
- [F-3] Email-alias normalization
- [F-9] Over-tier apply
- [F-13] Self-approval (low likelihood but unhandled)

**Low (prod-side confirms):**
- [F-10] `display_errors` in prod
- [F-11] nginx log file permissions
- [F-12] PHP-FPM timeouts + worker count

Each finding gets a Tier-1+ disposition: fix / accept-as-risk / defer-with-date.
