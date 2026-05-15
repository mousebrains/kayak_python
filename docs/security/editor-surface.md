# Editor surface inventory

> **Drafted:** 2026-05-12 against `main` at `9446e51`. Part of `docs/done/PLAN_editor_security_review.md` Tier 0. Reading order: this doc enumerates the surface; [threat-model.md](threat-model.md) enumerates risks against it; [controls-map.md](controls-map.md) maps existing controls to those risks; [findings.md](findings.md) tracks gaps.
>
> This doc is updated when a new PHP endpoint, DB table, cookie, rate limit, or external service joins the editor pipeline. Stale-data risk is highest for the "Cross-tier counts" sub-section.

## Scope

Every code path that touches the editor account model (`editor`, `editor_session`, `editor_magic_link`, `maintainer_credential`) or the proposal pipeline (`change_request`, `change_request_attachment`, `edit_history`). Read-only public pages (state HTML, sparklines, map.html, reach.php, picker.php) are out of scope except where they render editor-aware UI.

## PHP entry points

10 entry points. All HTTP responses include the headers from `/etc/nginx/snippets/security-headers.conf` or `security-headers-turnstile.conf` (latter for `login.php` and `contact.php`).

| File | URL | Auth | CSRF | GET params | POST params | DB read | DB write | External | Output |
|---|---|---|---|---|---|---|---|---|---|
| `account.php` | `/account.php` | require_editor | POST | — | action, display_name | editor | editor | — | HTML |
| `admin.php` | `/admin.php` | require_maintainer | POST | status (enum) | action (8 variants), id, ids[], display_name | editor, change_request, editor_session | editor, editor_session | — | HTML |
| `auth.php` | `/auth.php` | none (token-gated) | POST | t (hex), next | csrf_token, t, next | editor_magic_link | editor_magic_link, editor_session, editor (last_login_at) | — | HTML interstitial → redirect |
| `comment.php` | `/comment.php` | require_editor | POST | — | csrf_token, subject, body, notes_to_maint, source_url, website (honeypot) | change_request | change_request | mail | HTML → redirect |
| `contact.php` | `/contact.php` | **none** | POST | — | csrf_token, from_email, subject, body, cf-turnstile-response, source_url, website (honeypot) | — | — | turnstile, mail | HTML |
| `edit.php` | `/edit.php` | require_maintainer | POST | id, type (reach\|gauge) | csrf_token, target_type, reach_id\|gauge_id, dynamic field set | reach\|gauge, edit_history | reach\|gauge, edit_history | — | HTML form → success page |
| `login.php` | `/login.php` | none (feature-gated) | POST | next | csrf_token, email, cf-turnstile-response, next | editor | editor, editor_magic_link | turnstile, mail | HTML form / info |
| `logout.php` | `/logout.php` | require_editor_feature | POST | — | csrf_token | — | editor_session (revoke) | — | HTML form → / |
| `propose.php` | `/propose.php` | require_editor (maintainer→`/edit.php`) | POST | type, id | csrf_token, target_type, target_id, tier-dependent fields, notes_to_maint, source_url, website | reach, reach_class, change_request | change_request | mail | HTML form / success |
| `review.php` | `/review.php` | require_maintainer | POST | id (optional), status (enum) | csrf_token, id, action (5 variants), reviewer_note, reach_<field>, classes, flow_low/high/data_type | change_request, editor (JOIN), reach\|gauge | change_request, edit_history | mail | HTML list / detail form |

Read-only consumers (referenced by the plan but out of inventory depth):

- `gauge.php` — public gauge detail; renders maintainer-only "Edit" link when `is_maintainer()`.
- `php/includes/header.php` — renders nav with `/login.php` (logged out) or `/account.php` + `/logout.php` (logged in); shows `Admin` link when `is_maintainer()`. Branches on `editor_feature_enabled()` + `current_editor()`.

### Cross-tier counts

Notable counts derived from above:

- **Maintainer-only endpoints:** 3 (`admin.php`, `edit.php`, `review.php`).
- **Editor-required endpoints:** 3 (`account.php`, `comment.php`, `propose.php`).
- **Unauthenticated PHP endpoints touching the editor pipeline:** 3 (`auth.php` — token-gated; `login.php` — feature-gated; `contact.php` — open; `logout.php` — feature-gated but the GET form does not mandate login).
- **POST endpoints (all require CSRF):** all 10.
- **Endpoints that send mail:** 4 (`comment.php`, `contact.php`, `login.php`, `review.php`).
- **Endpoints that consume Turnstile:** 2 (`login.php`, `contact.php`).
- **Endpoints that write to `edit_history`:** 2 (`edit.php`, `review.php`).

## DB tables holding PII, credentials, or auth-relevant data

Per-column classification:
- **public** — fine to expose to anyone
- **internal** — application metadata, not publicly visible but not directly sensitive
- **PII** — identifies a person (email)
- **credential** — auth secret material (token hash, session hash, magic-link hash, WebAuthn public key)

### `editor` (`src/kayak/db/models.py:639`)

| Column | Type | Class | Purpose |
|---|---|---|---|
| `id` | int PK | internal | row id |
| `email` | varchar(255) UNIQUE | **PII** | login identity; also used as recipient for magic links |
| `display_name` | varchar(128) | PII (self-supplied) | rendered in UI |
| `status` | enum (pending/minimal/full/banned/maintainer) | internal | auth role; gates `is_maintainer()` |
| `request_note` | text | internal (user-supplied) | optional note on initial signup |
| `created_at` | datetime | internal | auditing |
| `reviewed_at` | datetime nullable | internal | when the maintainer last actioned this editor |
| `reviewed_by` | int FK editor.id | internal | who actioned |
| `last_login_at` | datetime nullable | internal | session telemetry |

### `editor_session` (`src/kayak/db/models.py:673`)

| Column | Type | Class | Purpose |
|---|---|---|---|
| `id` | int PK | internal | row id |
| `editor_id` | int FK editor.id (CASCADE) | internal | session owner |
| `token_hash` | varchar(64) UNIQUE | **credential** | sha256(`ed_sess` cookie value); never the raw token |
| `created_at` | datetime | internal | issued at |
| `expires_at` | datetime | internal | 7-day flat absolute |
| `last_seen_at` | datetime nullable | internal | for session activity surface in `account.php`/`admin.php` |
| `ip` | varchar(45) | **PII** | requester IP at session creation (IPv6-capable) |
| `user_agent` | varchar(512) | **PII** | requester UA at creation |
| `revoked_at` | datetime nullable | internal | logout/admin-revoke marker |

### `editor_magic_link` (`src/kayak/db/models.py:702`)

| Column | Type | Class | Purpose |
|---|---|---|---|
| `id` | int PK | internal | row id |
| `editor_id` | int FK editor.id (CASCADE) | internal | recipient |
| `token_hash` | varchar(64) UNIQUE | **credential** | sha256(64-char hex token); raw token only ever in transit and in the email |
| `created_at` | datetime | internal | issued at |
| `expires_at` | datetime | internal | 30-min absolute (`MAGIC_LINK_TTL` in `php/includes/auth.php`) |
| `used_at` | datetime nullable | internal | single-use marker; non-null = burnt |
| `ip_issued` | varchar(45) | **PII** | requester IP at issuance |
| `next_url` | varchar(512) | internal | post-login redirect target; pre-validated via `safe_next_url()` |

### `maintainer_credential` (`src/kayak/db/models.py:731`) — **schema only; not wired**

| Column | Type | Class | Purpose |
|---|---|---|---|
| `id` | int PK | internal | row id |
| `editor_id` | int FK editor.id (CASCADE) | internal | credential owner (must be a maintainer) |
| `credential_id` | varchar(255) UNIQUE | **credential** | WebAuthn credential id (raw, transmittable per spec) |
| `public_key` | text | **credential** | WebAuthn COSE public key |
| `sign_count` | int | internal | replay-defense monotonic counter |
| `transports` | varchar(128) | internal | hint set: usb/nfc/ble/internal |
| `nickname` | varchar(64) | PII (user-supplied) | "phone", "laptop", "yubikey-blue" |
| `created_at` / `last_used_at` / `revoked_at` | datetime | internal | lifecycle |

**Status:** The table exists. Phase 1b registration + assertion endpoints don't. Editor docstring at `models.py:639` claims "maintainer status … uses strong auth (WebAuthn) rather than magic links" — this is aspirational, not current. Maintainers in practice log in via `/login.php` magic-link, same as editors. See [FINDING-5] in `findings.md`.

### `change_request` (`src/kayak/db/models.py:761`)

| Column | Type | Class | Purpose |
|---|---|---|---|
| `id` | int PK | internal | row id |
| `target_type` | enum (`reach`/`gauge`/`source`/`site`) | internal | what the proposal touches |
| `target_id` | int nullable | internal | row id within target_type; NULL for `site` (general comment) |
| `editor_id` | int FK editor.id (CASCADE) | internal | proposer |
| `submitted_at` | datetime | internal | rate-limit anchor |
| `subject` | varchar(256) | internal (user-supplied) | comment-only |
| `payload_json` | text | **internal (user-supplied, untrusted)** | proposal payload — rendering raw is XSS; must be html-escaped on output. Shape depends on target_type. |
| `notes_to_maint` | text | internal (user-supplied) | proposer→reviewer freeform note |
| `status` | enum (pending/approved/rejected/resolved) | internal | workflow state |
| `reviewed_at` | datetime nullable | internal | when reviewed |
| `reviewed_by` | int FK editor.id (SET NULL) | internal | which maintainer reviewed |
| `applied_json` | text nullable | internal | exact JSON written on approve (after any maintainer tweaks) |
| `reviewer_note` | text nullable | internal | feedback shown to proposer |

### `change_request_attachment` (`src/kayak/db/models.py:804`) — **schema only; not wired**

| Column | Type | Class | Purpose |
|---|---|---|---|
| `id` | int PK | internal | row id |
| `change_request_id` | int FK change_request.id (CASCADE) | internal | parent proposal |
| `filename` | varchar(256) | internal (user-supplied) | original upload filename |
| `content_type` | varchar(128) | internal | MIME (claimed by upload, must be validated) |
| `size_bytes` | int | internal | enforce a max |
| `sha256` | varchar(64) | internal | content addr; storage key |
| `storage_path` | varchar(512) | internal | on-disk path under uploads root |
| `caption` | text | internal (user-supplied) | proposer note |
| `uploaded_at` | datetime | internal | retention anchor |

**Status:** Zero rows expected (no upload endpoint exists). The plan defers Phase 3.3 file-upload audit until the endpoint lands.

### `edit_history` (`src/kayak/db/models.py:836`)

| Column | Type | Class | Purpose |
|---|---|---|---|
| `id` | int PK | internal | row id |
| `target_type` | enum (same as change_request) | internal | which table changed |
| `target_id` | int nullable | internal | row id within target_type |
| `change_request_id` | int FK change_request.id (SET NULL) | internal | NULL for direct maintainer edits via `/edit.php` |
| `field` | varchar(64) | internal | column name |
| `old_value` | text nullable | internal | before |
| `new_value` | text nullable | internal | after |
| `changed_at` | datetime | internal | when |
| `changed_by` | varchar(64) | internal | `'maintainer:<id>'` or `'editor:<id>'` |

**No tamper-resistance** — no hash chain, no `previous_hash`, no external sink. See [FINDING-4] in `findings.md`.

## Cookies

Both cookies are set via `_cookie_params()` in `php/includes/auth.php` with the attributes:

- `HttpOnly` — JS cannot read either cookie.
- `SameSite=Strict` — neither cookie is sent on cross-origin requests (incl. clicks from external links/emails).
- `Secure` when `$_SERVER['HTTPS']` is set — confined to HTTPS.
- `path=/` — sent for every request.

| Cookie | Lifetime | Contents | Set by | Cleared by |
|---|---|---|---|---|
| `ed_sess` | 7-day flat absolute | 64-hex `random_bytes(32)` session token (raw); server stores sha256 in `editor_session.token_hash` | `set_editor_session()` (post-magic-link consumption in `auth.php`) | `clear_editor_session()` (in `logout.php`); maintainer can revoke via `admin.php` |
| `ed_csrf` | session only (no expires) | 64-hex `random_bytes(32)` CSRF token (raw); not stored server-side — double-submit pattern | `csrf_token()` first-call (lazy); rotated on session creation in `set_editor_session()` | not actively cleared on logout — orphaned `ed_csrf` is harmless (no matching `ed_sess`) |

### Verification points

- Logout revokes server-side (`editor_session.revoked_at`) AND clears the cookie. Cookie replay alone after logout = 401.
- CSRF rotates on login (defends against session fixation pre-auth → post-auth).
- Magic-link consumption is the only write path to `editor_session.token_hash`.

## Rate limits and abuse posture

Three layers + application-side throttles + daily caps.

### Layer 1 — nginx `limit_req` (per-IP, key=`$binary_remote_addr`)

Zones in `deploy/ratelimit.conf`; bindings in `deploy/levels`:

| Zone | Rate | Burst | Used by |
|---|---|---|---|
| `login` | 3 r/min | 2 | `/login.php` |
| `auth` | 10 r/min | 4 | `/auth.php`, `/logout.php` |
| `edit` | 5 r/min | 2 | `/edit.php` |
| `contact` | 10 r/min | 4 | `/contact.php` |
| `php` | 5 r/s | 5 | `/account.php`, `/propose.php`, `/comment.php`, `/review.php`, `/admin.php` |
| `global` | 20 r/s | 40 | every location |

All keys are `$binary_remote_addr` — pure per-IP, no per-account dimension.

### Layer 2 — fail2ban

Jails in `deploy/fail2ban/jail.d/`:

- `kayak-edit.conf` — watches nginx error log for `/edit.php` 4xx bursts.
- `kayak-editor-auth.conf` — watches for `/auth.php` and `/login.php` 4xx bursts.

Filters in `deploy/fail2ban/filter.d/`: `nginx-edit-auth.conf`, `nginx-editor-auth.conf`, `nginx-default-block.conf`, `nginx-malicious.conf`.

Default action: 1h ban with `bantime.increment=true`, capping at 1w.

### Layer 3 — Cloudflare Turnstile

Required on POST handlers in `login.php` and `contact.php`. Verified server-side via `php/includes/turnstile.php` against Cloudflare's `siteverify` endpoint. Bypasses available only in test mode (per `TurnstileTest.php`).

### Layer 4 — application-side throttles

- **`magic_link_under_throttle()`** in `php/includes/auth.php` — 5 magic links per email per hour, 20 magic links per IP per hour. Per-email cap blunts targeted enumeration; per-IP cap covers shared households without locking out the household.
- **`comment.php` daily cap** — 5 site-comments per editor per day.
- **`propose.php` tiered daily cap** — 3 (pending) / 10 (minimal) / 20 (full) proposals per editor per day; maintainers unlimited (9999).

### Verification gap

- All nginx layers are per-IP only. A botnet rotating IPs sails past layer 1; layer 2 catches repeat 4xx but only after a burst; layer 3 is the catch-all but only on the gated endpoints.
- No per-account lockout exists. An attacker who's burnt 5 magic-link emails for a target email cannot try more for an hour but can try another target.

## External services

| Service | How called | Where configured | Data sent |
|---|---|---|---|
| msmtp → Gmail SMTP relay | `send_email()` in `php/includes/mail.php` | local msmtp config (`~/.msmtprc` or system) | magic-link emails (token in URL), maintainer notifications (proposer email, subject, body) |
| Cloudflare Turnstile | client-side widget + server-side `siteverify` POST via `verify_turnstile()` in `php/includes/turnstile.php` | site key + secret env vars (per `TurnstileTest.php`) | client IP + opaque token |

No third-party analytics. No third-party error tracking. No CDN for editor-pipeline assets (Cloudflare in front of the whole vhost, but no Cloudflare features beyond Turnstile are used).

## Logging surface

| Log | Path | Captured | Retention |
|---|---|---|---|
| nginx access | `/var/log/nginx/kayak-access.log` (per `deploy/levels:329`) | full `$request` (incl. `/auth.php?t=<token>`), remote_addr, request_time, status, body_bytes_sent, http_referer, http_user_agent | logrotate default (TBD; confirm in Tier 0.4 with `cat /etc/logrotate.d/nginx`) |
| nginx error | `/var/log/nginx/kayak-error.log` | upstream errors, rate-limit-trip rejections (used by fail2ban) | same |
| PHP-FPM error | per pool config | unhandled PHP errors / warnings | same |
| systemd journal | `journalctl -u kayak-*` | timer + service lifecycle | journal default |

**Magic-link tokens land in nginx access log** via `$request`. Single-use + 30-min expiry mitigates, but log-disclosure → live-token risk for newly-issued unconsumed tokens. See [FINDING-2] in `findings.md`.

## File uploads

**N/A.** `change_request_attachment` schema is in place but no PHP endpoint accepts uploads. Verified: `grep -rn "move_uploaded_file|\$_FILES" php/` returns nothing. Tier 3.3 stays N/A; activates when the endpoint lands.

## Auth-aware feature flag

`editor_feature_enabled()` in `php/includes/auth.php` gates the editor pipeline. Some endpoints (`logout.php`, `login.php`) wrap their auth requirements in this flag so the system can be disabled at the per-page level without code removal. `contact.php` is intentionally NOT gated (the contact form should work even when the editor pipeline is disabled — its footer link is shown unconditionally).

## Open verification items (Tier 0.4 confirms)

- HSTS active in live nginx? (`deploy/levels` doesn't include the directive; `deploy/SETUP.md:395` shows the intended line; prod-side `sudo nginx -T` needed)
- nginx access-log rotation cadence (default vs custom)
- fail2ban filter regex match against current error-log format
- CSP details on each endpoint (snippet contents live on prod only, not in `deploy/`)
