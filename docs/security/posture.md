# Editor pipeline — security posture summary (2026-05-12)

> Final posture doc produced by Tier 6 of `docs/done/PLAN_editor_security_review.md`. Snapshot of what controls exist, what's accepted/deferred and why, and the operator's standing obligations. Update on major changes (per D-T5.3) or during the annual light-touch re-review (next: ~2027-05-12).

## At a glance

- **5 findings Closed** by code or config changes in Tier 6.
- **6 findings Accepted** with documented rationale + re-evaluation triggers.
- **2 findings Deferred** with explicit trigger (second-maintainer scenario).
- **3 findings Open** — all prod-side confirmations the operator must verify by running shell commands on the live host. Each has a one-line repro command in [findings.md](findings.md).
- **11 decisions** documented in [decisions.md](decisions.md) spanning Tiers 1–5.

The site is now in a documented, defensible posture for hobby/club scale. The remaining open items are operator-side confirmations, not gaps to fix.

## Authentication + authorization

| Asset | Control | Source |
|---|---|---|
| Magic-link token | 256-bit random_bytes(32), sha256 at rest, 30-min expiry, GET-peek/POST-consume split (email-scanner defense) | `php/includes/auth_magic_link.php` + `php/auth.php` |
| Magic-link rate limit | 5/hour per email + 20/hour per IP | `magic_link_under_throttle()` |
| Magic-link token in logs | Redacted via nginx `map` directives (F-2 fix) | `deploy/kayak-log-format.conf` |
| Magic-link token in browser Referer | `Referrer-Policy: no-referrer` on auth.php (F-14 fix) | `php/auth.php` |
| Session cookie | `ed_sess`, HttpOnly + SameSite=Strict + Secure-on-HTTPS, 7-day flat expiry, sha256 hash at rest | `php/includes/auth.php` |
| Session revocation | Logout sets `revoked_at`; `current_editor()` filters `revoked_at IS NULL`; regression test guards (F-15 fix) | `tests/php/SessionRevocationTest.php` |
| CSRF | Double-submit cookie via `hash_equals` constant-time compare | `require_csrf()` in auth.php |
| Maintainer auth | Same magic-link flow (D-T1.3 Accepted Option A) | `decisions.md` |
| Maintainer promotion | CLI-only (`levels seed-maintainer`); no web path | `src/kayak/cli/seed_maintainer.py` |
| Role enforcement | `require_editor()` / `require_maintainer()` at every privileged endpoint | Tier 2.1 audit verified all 10 endpoints |
| Authorization checks | IDOR sweep verified; per-editor scoping on proposal listing | Tier 2.2 audit |
| Audit trail | `edit_history` rows with `changed_by='editor:<id>' / 'maintainer:<id>'`; survives editor row deletion intentionally | `decisions.md` D-T4.3a + D-T2.4 |

## Transport + network layer

| Asset | Control | Source |
|---|---|---|
| HTTPS | nginx + Let's Encrypt | `deploy/SETUP.md` § 8 |
| HSTS | `max-age=63072000; includeSubDomains` (F-1 fix; preload OFF) | `conf/security-headers.conf` |
| Security headers (general) | `/etc/nginx/snippets/security-headers.conf` (CSP, X-Frame-Options, etc.) | nginx snippet (host-side) |
| Rate limits | nginx `limit_req` zones: `login:3r/min`, `auth:10r/min`, `edit:5r/min`, `contact:10r/min`, `php:5r/sec`, `global:20r/sec`, all per-IP | `deploy/ratelimit.conf` |
| Captcha | Cloudflare Turnstile on `/login.php`, `/contact.php` | `deploy/secrets.env` for secret |
| Brute force / abuse | fail2ban + nginx limit_req + Turnstile + application throttles (4-layer defense) | Tier 3.4 audit |

## Input/output handling

| Asset | Control | Source |
|---|---|---|
| XSS | All render paths escape via `htmlspecialchars()` (PHP 8.1+ default `ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML401`); convention documented inline | Tier 3.1 audit + `php/includes/html.php:6-13` |
| SQLi | 131 `prepare()` calls verified parameterized; all `->query()` / `->exec()` calls use static SQL; 9 SQL-concat sites have cross-file invariant whitelists | Tier 3.2 audit |
| File upload | None today — `change_request_attachment` schema present but no PHP endpoint (D-T3.3 deferred) | n/a |

## User-data obligations

| Obligation | Mechanism | Source |
|---|---|---|
| Account deletion | Manual via `levels delete-editor` CLI (D-T4.1) | `src/kayak/cli/delete_editor.py` |
| Data export | On-request via `levels export-editor` CLI (D-T4.2) | `src/kayak/cli/export_editor.py` |
| Retention purge — magic links | 90-day TTL post-expiry, daily timer (D-T4.3b) | `src/kayak/cli/editor_retention.py` + `systemd/kayak-editor-retention.timer` |
| Retention purge — sessions | 90-day TTL post-expiry, daily timer (D-T4.3c) | same |
| Audit-trail PII linkage | Severed at deletion time, not via decay (D-T4.3a) | `delete_editor` `--anonymize-history` flag |
| Privacy policy | Refreshed Tier 6 (F-16); accurate Data We Collect ↔ Your Rights | `php/privacy.php` |
| Terms of service | Not provided (D-T4.4 Accepted) | n/a |

## Disclosure + response

| Asset | Control | Source |
|---|---|---|
| Vulnerability disclosure | `security.txt` Contact + Expires + Preferred-Languages (D-T5.1 Option A; GHSA deferred to first-report trigger) | `static/security.txt` |
| IR cadence | Best-effort (D-T5.2); concrete commitments in runbook | `docs/security/incident-response.md` |
| IR runbook | Discovery + triage + containment (C1-C5) + credential rotation + user notification + post-incident review | `docs/security/incident-response.md` |
| Re-review cadence | Major-change-triggered + annual light-touch (D-T5.3) | this file's update trigger |
| Backup-restore drill | Plan documented; execution operator-scheduled | `tier5-audit.md` § Phase 5.5 |

## Accepted findings — re-evaluation triggers

These items have explicit triggers that flip them out of Accepted status. Operator should check at each annual review:

| ID | Accepted as | Re-eval trigger |
|---|---|---|
| F-3 | Email-alias normalization is low-impact at hobby scale | Observed alias-abuse; per-account daily cap introduced; community charter starts asserting one-account-per-person |
| F-4 | `edit_history` tamper-resistance — backups + web-side controls suffice | Incident with suspected tampering; second maintainer; compliance regime appears |
| F-5 | Maintainer magic-link only (no 2FA) — Gmail 2FA is the gate | Second maintainer; F-4 implemented; new privileged web operation added; maintainer-account incident |
| F-6 | `htmlspecialchars()` convention documented; PHP 8.1+ defaults adequate | Drop to PHP < 8.1; convention violated by new code |
| F-7 | Mass-assignment whitelists cross-file-verified safe | New tier-whitelisted field; refactor that breaks the keys→fields invariant |
| F-8 | SQL string concat is code-smell only; safe in current usage | `docs/done/PLAN_php_layer_split.md` re-activates; new caller adds less-trusted source; future re-audit finds drift |

## Deferred findings — trigger conditions

| ID | Deferred trigger |
|---|---|
| F-9 | Second maintainer joins (reach_class over-tier becomes meaningful when there are two maintainers to audit each other) |
| F-13 | Second maintainer joins (self-approval prevention is moot at single-maintainer scale where direct edit is always available) |

Both F-9 and F-13 belong to the same "second-maintainer family" as D-T1.3 (Maintainer 2FA), D-T2.4 (audit tamper-resistance), F-5. When that trigger fires, address them as a coordinated set.

## Operator standing checklist

Recurring obligations from the security review:

| Cadence | Item | Source |
|---|---|---|
| Daily | Check security gmail (`pat.kayak@gmail.com`) | D-T5.2 / incident-response.md |
| Daily (auto) | `kayak-editor-retention.timer` runs the purge | D-T4.3 |
| 2027-04-01 | Refresh `static/security.txt` Expires line to 2028-05-20 (+1 year) | D-T4.5 |
| ~2027-05-12 | Annual light-touch re-review (~half-day): re-read findings.md + decisions.md, check re-eval triggers, run F-10/11/12 confirms, refresh README | D-T5.3 |
| At first convenience | First backup-restore drill from security angle | tier5-audit.md § Phase 5.5 |
| At any major change | Focused re-review of the affected slice | D-T5.3 |

## Out-of-scope items

Documented in `docs/done/PLAN_editor_security_review.md` under "Out of scope":
- External pentest, WAF, code-signing / SBOM, compliance certifications, DDoS protection beyond Hetzner default, PHP-layer code refactor (tracked separately in `docs/done/PLAN_php_layer_split.md`).

## Update triggers for this doc

Update `posture.md` when:
- A major change per D-T5.3 happens.
- The annual light-touch re-review runs.
- An accepted finding is reclassified.
- A deferred finding is triggered.
- A new finding is filed or closed.
