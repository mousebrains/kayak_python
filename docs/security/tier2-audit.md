# Tier 2 — Authorization review audit log

> **Started:** 2026-05-12 against `main` at `aadb63c`. Per `docs/done/PLAN_editor_security_review.md` Tier 2 verification gate: "Authorization matrix documented (role × endpoint → expected response); each row tested; IDOR sweep produced no findings (or each one is filed)."
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
| `/login.php` | 200 (form) | redirect to /account.php (per `src/kayak/web/php/login.php` "redirects if already logged in" note) | redirect to /account.php | not applicable — banned editor cannot log in (issue_magic_link short-circuits) |
| `/logout.php` | 200 (idle form; no-op POST) | 200 → revokes session → 302 / | 200 → revokes session → 302 / | 200 (no session to revoke) |
| `/propose.php` | 302 → /login.php | 200 (tier-gated fields) | 302 → /edit.php (maintainers bounced) | 302 → /login.php |
| `/review.php` | 302 → /login.php | 403 | 200 | 302 → /login.php |

### Audit tests

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 2.1.1 | Role checks are present | ✅ | `require_editor()` or `require_maintainer()` called at top of every gated endpoint:<br>• `account.php:16`, `admin.php:17`, `comment.php:19`, `edit.php:28`; the `src/kayak/web/php/propose.php` shim still calls `require_editor()` and the `src/kayak/web/php/review.php` shim still calls `require_maintainer()` before delegating to their handlers<br>Feature gate via `require_editor_feature()`: `auth.php:20`, `login.php:18`, `logout.php:14`. |
| 2.1.2 | Role checks use a consistent helper (not ad-hoc) | ✅ | All 6 require-* calls use the helper functions from `src/kayak/web/php/includes/auth.php`. No file checks `current_editor()` directly when access is required-and-fail-out (the proper helper combines fetch + redirect/403). |
| 2.1.3 | Role checks precede data access | ✅ | In every endpoint, `require_X()` is called BEFORE any DB read or output. Verified line-by-line in Phase 0.1 inventory + spot-checks here. Order is consistent: feature-gate → role-check → CSRF-check (POST only) → handler body. |
| 2.1.4 | Banned-editor enforcement | ✅ | `current_editor()` SQL filter `e.status != 'banned'` (`src/kayak/web/php/includes/auth.php:current_editor`). Banned editors are indistinguishable from unauth in `require_editor()` — they get 302'd to login. login.php then short-circuits on banned in `issue_magic_link()` ("banned" → returns `editor_id => 0` without sending an email; UX is "same response as new editor" so no enumeration leak). |
| 2.1.5 | Maintainer-only enforcement | ✅ | `require_maintainer()` chains `require_editor()` then checks `is_maintainer($ed)`. On failure: 403 page with the user's email + status (informational only). Cannot be bypassed without already holding a session cookie + the editor row being status='maintainer'. |
| 2.1.6 | Response code semantics | ⚠ (note, not finding) | Plan's "401/200/200" simplification: actual response is **302 → /login.php** for unauth (not 401). For browser-facing endpoints this is correct UX; for non-browser clients it's unconventional but acceptable. No machine-consumed editor endpoints (json/api) exist in the editor pipeline; `api.php`/`data.php`/`latest.php` are out of scope for this tier. |

### Notes

- The `auth.php` token-only flow doesn't follow the require-X pattern — it's gated by the token (peek/consume) instead. The token IS the credential here; the resulting session is created server-side by `set_editor_session()` post-consume.
- `current_editor()`'s SQL filter combines four conditions (`token_hash`, `revoked_at IS NULL`, `expires_at > now`, `status != 'banned'`). Any single condition failure → null → `require_editor()` redirects. Strong single-point-of-truth pattern.
- `is_maintainer()` is a string comparison on `$ed['status']`. Cannot be forged client-side because `$ed` comes from `current_editor()` which reads from the DB.

### Phase 2.1 closeout

- ✅ Authorization matrix documented; all 6 audit tests pass (1 with a non-finding note about response-code semantics).
- No new findings; existing F-7/F-8/F-13 carry forward to 2.3.

## Phase 2.2 — IDOR sweep

**Verdict:** ✅ (no findings)

### ID-taking endpoints in scope

| Endpoint | GET param | POST param | Scope of read/write |
|---|---|---|---|
| `/account.php` | — | — | session-owner only (`$ed['id']` from `current_editor()`) |
| `/admin.php` | — | `id`, `ids[]` | maintainer-required; can act on any non-maintainer editor row (by design) |
| `/comment.php` | — | — | INSERT only; `editor_id` = current editor; `target_type='site'`, `target_id=NULL` |
| `/edit.php` | `id`, `type` | `reach_id`, `gauge_id`, `target_type` | maintainer-required; can edit any reach/gauge row (by design); `$type` whitelisted to `['reach','gauge']`; `$id` unified via `?:` chain |
| `/propose.php` | `type`, `id` | `target_type`, `target_id` | editor-required; existing-proposal lookup is scoped `WHERE editor_id = ? AND target_type = 'reach' AND target_id = ?` (per `src/kayak/web/php/includes/propose_handler.php :: _load_propose_context`); INSERT/UPDATE (in `_handle_propose_post`) uses `$ed['id']` for editor_id |
| `/review.php` | `id`, `status` | `id`, `action` | maintainer-required; can read/write any change_request (by design) |

Out of scope for this tier (public-facing reads, no editor scope): `description.php`, `gauge.php`, `api.php`, `latest.php`, `plot.php`, `reach.php`, `picker.php`, `gauge_picker.php`, `custom.php`, `custom_gauges.php`, `data.php`. Their model is "any reach/gauge is public" — documented here so future audits don't re-flag.

### Audit tests

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 2.2.1 | `propose.php` GET — can an editor see another editor's pending proposal? | ✅ | Pre-populated form is loaded via the existing-proposal SELECT (`src/kayak/web/php/includes/propose_handler.php :: _load_propose_context`), scoped by `editor_id = ?` from `current_editor()`. If editor X queries `?id=42` (a reach), they see their own pending proposal for reach 42, or no pre-population. They cannot see editor Y's proposal for the same reach. |
| 2.2.2 | `propose.php` POST — can an editor write a proposal as a different editor? | ✅ | INSERT (`src/kayak/web/php/includes/propose_handler.php :: _handle_propose_post`) uses `$ed['id']` from `current_editor()` for `editor_id`. The POST `target_id` is the reach id (public). No path lets editor X set `editor_id=Y`. |
| 2.2.3 | `edit.php` GET/POST id-mismatch — can attacker write to a row they didn't view? | ✅ | The handler reads `$id` from GET `?id` or POST `reach_id`/`gauge_id` (priority chain via `?:`). The POST id is used for the UPDATE; an attacker who changes the POST id is editing a different row than they viewed on GET — but maintainers can edit any row anyway. No privilege escalation. |
| 2.2.4 | `edit.php` `$type` whitelist | ✅ | `src/kayak/web/php/edit.php:30-33`: `if (!in_array($type, ['reach', 'gauge'], true)) { http_response_code(400); exit('Unsupported edit target type'); }`. No way to pass `$type='editor'` or similar. |
| 2.2.5 | `review.php` proposal access scope | ✅ | The GET detail read `SELECT … FROM change_request cr JOIN editor e … WHERE cr.id = ?` (`src/kayak/web/php/includes/review_handler.php :: _render_review_detail`) and the POST action read `SELECT * FROM change_request WHERE id = ?` (`src/kayak/web/php/includes/review_handler.php :: _review_handle_post`) are both unscoped by editor — but maintainer is REQUIRED (the `src/kayak/web/php/review.php` shim calls `require_maintainer()` before delegating to `handle_review_request`), so unrestricted access is the intended model. List view (`src/kayak/web/php/includes/review_handler.php :: _render_review_list`) joins editor for display; status filter is enum-validated. |
| 2.2.6 | `admin.php` editor-target scope | ✅ | All 8 POST actions take an `id` (or `ids[]`) representing the target editor. Maintainer-required (line 17). The `ban` action additionally guards `status != 'maintainer'` (line 84) — compromised maintainer cannot demote another maintainer via web. |
| 2.2.7 | Cross-target type confusion (e.g., POST `target_type='gauge'` on propose.php) | ✅ | `propose.php`'s GET is hardcoded `type='reach'` (the file's `$type = 'reach'` near top); POST INSERT hardcodes `target_type='reach'`. Type confusion would require post-fact code changes; not a current vector. |

### Notes

- **All write paths use `$ed['id']` from `current_editor()` for editor scoping.** No POST-body editor_id is ever read. This is a strong, consistent pattern.
- **All read paths to editor-owned data are filtered by `editor_id`** (existing-proposal lookup in propose.php is the only such read).
- **Maintainer endpoints intentionally have global read/write scope.** The "IDOR" framing collapses to "is the maintainer check present and correct?" — already answered in Phase 2.1.
- **`change_request_attachment`** is not yet wired (no upload endpoint), so its IDOR posture is N/A for now. When the upload endpoint lands, an IDOR audit should re-check: can editor X read editor Y's attachment via `id` enumeration?

### Phase 2.2 closeout

- ✅ Authorization model is clean — no IDOR vectors found.
- All ID-taking editor-pipeline endpoints either scope by `current_editor()['id']` (editor-owned data) or require maintainer (global scope, by design).

## Phase 2.3 — Privilege escalation paths

**Verdict:** ✅ for the critical-bucket threats (T-T3/T-E1 mass-assignment, T-T4/T-E2 SQLi); ⚠ for F-7 (refined, partially closes) / F-8 (code smell stands) / F-9 (refined, near-false-positive) / F-13 (confirmed, low-impact for single-maintainer).

### Audit tests

| # | Test | Verdict | Evidence |
|---|---|---|---|
| 2.3.1 | Can editor promote self to `status='maintainer'` via mass-assignment? | ✅ | The only path to maintainer status is the CLI `levels seed-maintainer` (Phase 1.3). No web action sets that status. `account.php` whitelists `display_name` only. `admin.php` is maintainer-required and its actions cap at `status='full'`. No editor-side input flows into the `status` column. |
| 2.3.2 | Can editor promote self via SQLi? | ✅ | All PDO usage in editor pipeline is parameterized. The two `UPDATE $table SET $sets` sites in `edit.php:117` and `review_logic.php:101` use whitelisted `$table` (`{reach,gauge}`) and whitelisted column names (verified via data-flow trace in 2.3.4 and 2.3.5 below). No user-controlled string lands in a `prepare()` directly. F-8 (code smell) remains tracked but is not an exploitable issue today. |
| 2.3.3 | Mass-assignment via `account.php` (F-7 subaudit, account) | ✅ | `src/kayak/web/php/account.php:22-29` switch-case accepts only `action='set_display_name'`; reads only `display_name` from POST; max 128 chars; updates only the `display_name` column for the current editor's `id`. No path writes other columns. |
| 2.3.4 | Mass-assignment via `propose.php` (F-7 subaudit, propose) | ✅ | Tier-gated field whitelist in `src/kayak/web/php/includes/propose_handler.php :: _load_propose_context`: `$reach_fields = $allow_full ? array_merge($text_fields, $full_reach_fields) : $text_fields` where `$text_fields=['description','features']` and `$full_reach_fields=['display_name', 'latitude_start', …]`. The POST loop in `src/kayak/web/php/includes/propose_handler.php :: _handle_propose_post` uses `foreach ($reach_fields as $f)` — iterates ONLY the whitelist, regardless of what additional keys the POST body contains. Coordinates further validated for numeric type. No path writes outside the whitelist. **F-7 refined: propose.php confirmed safe.** |
| 2.3.5 | Mass-assignment via `review.php` (F-7 subaudit, review) | ✅ | The maintainer's apply form constructs `$applied['reach']` via `foreach (array_keys($payload['reach']) as $f) { … }` (`src/kayak/web/php/includes/review_handler.php :: _review_build_approve_payload`). The KEY SET is constrained to what the proposer's `payload_json` already contains. The maintainer can OVERRIDE the value via POST `reach_<field>`, but cannot ADD keys not in the original payload. Since `payload_json` was tier-constrained at submission time (2.3.4 above), the maintainer cannot apply fields outside the proposer's tier scope **for reach fields**. **F-7 refined: review.php confirmed safe for reach fields.** |
| 2.3.6 | Mass-assignment via `edit.php` (F-7 subaudit, edit) | ✅ | Already verified (Tier 0/1): `$editable_fields` whitelist; per-field comparison + UPDATE; numeric fields validated; empty submissions skipped. |
| 2.3.7 | `UPDATE $table SET $sets` SQL-concat code smell (F-8) | ⚠ | Two sites: `edit.php:117`, `review_logic.php:101`. Both use whitelisted `$table` and whitelisted column names; safe in current usage. The pattern relies on cross-file invariants (proposer's tier whitelist → payload_json keys → applied keys → SQL column names). A future contributor adding a new write path could break the invariant. **F-8 not exploitable today; refactor candidate when payload-handling cluster gets touched.** |
| 2.3.8 | Self-approval prevention (F-13) | ⚠ | `review_approve()` in `src/kayak/web/php/includes/review_logic.php:61` takes `$cr, $applied, $maint_id`. Does NOT check `$cr['editor_id'] !== $maint_id`. A maintainer with pre-promotion pending proposals can approve their own work. **Bypass of "second pair of eyes" review intent**. For the current single-maintainer posture, this is moot (the maintainer could also direct-edit via `/edit.php`, achieving the same outcome). Becomes meaningful when a second maintainer joins — same trigger as F-5. **F-13 confirmed; low impact at single-maintainer scale.** |
| 2.3.9 | Over-tier apply (F-9) — reach fields | ✅ (near-false-positive) | Per 2.3.5: the apply key set is constrained to `array_keys($payload['reach'])`, which is constrained at submission to `$reach_fields` per the proposer's tier. **Reach-field over-tier apply is NOT possible.** F-9 partially refines: the original concern was based on the assumption that the maintainer could add keys; in fact the form template only renders keys the proposer included. |
| 2.3.10 | Over-tier apply (F-9) — reach_class | ⚠ | `$applied['reach_class']` is built from POST `classes_present`/`classes`/`flow_low`/etc. (`src/kayak/web/php/includes/review_handler.php :: _review_build_approve_payload`), independent of `$payload`. The maintainer can ADD class changes even when the proposer didn't propose class edits. BUT: the change is recorded in `edit_history` with `changed_by='maintainer:<id>'` and `change_request_id` linkage. Audit trail is technically correct. The semantic concern (was this class change part of the original proposal?) survives but is recoverable from the audit trail. **F-9 refined: applies only to reach_class; mitigated by audit attribution.** |
| 2.3.11 | File-upload privileged-path injection | ⊘ | No upload endpoint exists yet; `$_FILES` / `move_uploaded_file` grep returns nothing. Activates when Phase 1b file-upload wiring lands. |
| 2.3.12 | SQL-injection into `maintainer_credential` | ⊘ | No PHP code writes to that table; only the (unwired) WebAuthn Phase 1b would. N/A. |
| 2.3.13 | Cross-tenant escalation (write to another editor's row) | ✅ | Confirmed in Phase 2.2 (IDOR sweep) — all writes use `current_editor()['id']` for editor scoping. No POST-body `editor_id` path. |

### Findings updates

- **F-7** — REFINED to clarify the whitelist invariant is intact for both propose.php and review.php. Pattern is correct; code smell tracked separately (F-8). Downgrade from "needs confirmation" to "confirmed safe; no immediate action."
- **F-8** — STANDS as code-smell refactor candidate; not exploitable.
- **F-9** — REFINED: applies only to `reach_class` (not reach fields). Mitigated by audit-trail attribution. Downgrade from "Medium" to "Low" in `findings.md`.
- **F-13** — CONFIRMED unhandled. Low impact at single-maintainer scale; same trigger as F-5 (revisit when adding a second maintainer).

### Phase 2.3 closeout

- ✅ All critical-bucket threats (T-T3, T-E1, T-T4, T-E2) confirmed safe.
- ⚠ F-7 closes (refined to "confirmed safe").
- ⚠ F-8 remains as code-smell refactor candidate.
- ⚠ F-9 downgraded to Low; reach_class apply is unconstrained but audit-trail-attributed.
- ⚠ F-13 confirmed; tied to multi-maintainer trigger.

## Phase 2.4 — Audit trail integrity

**Verdict:** ⚠ F-4 confirmed (no tamper-resistance); decision point setup.

### Audit observations

| # | Check | Verdict | Evidence |
|---|---|---|---|
| 2.4.1 | `edit_history` schema includes a hash chain or `previous_hash` column? | ❌ | Per-row schema (`src/kayak/db/models.py:836`): `id, target_type, target_id, change_request_id, field, old_value, new_value, changed_at, changed_by`. No hash, no chain, no sequence anchor. |
| 2.4.2 | Append-only constraint in DB (trigger / rule)? | ❌ | SQLite has no native append-only triggers. Could be approximated via `BEFORE DELETE/UPDATE` trigger that raises, but none exist. |
| 2.4.3 | Out-of-band sink (file / external service / DB)? | ❌ | Repo-wide grep: no code writes audit rows to any sink other than the `edit_history` table. No `~/logs/edit_audit.log`, no S3 ship, no syslog. |
| 2.4.4 | `change_request_id` linkage preserves attribution to proposal-vs-direct-edit? | ✅ | `edit_history.change_request_id` is NULL for direct maintainer edits via `/edit.php`, populated for approval-applied changes. Two paths trace cleanly. |
| 2.4.5 | `changed_by` attribution is per-row and correctly identifies maintainer-vs-editor? | ✅ | `'maintainer:<id>'` written for review-approval + direct maintainer edits. `'editor:<id>'` reserved for future paths (no current path writes editor rows directly; editor changes ride through the change_request → approve flow). |

### Current threat exposure

Operator-level threat (assumed trusted; out of scope for this plan):
- Operator runs `DELETE FROM edit_history WHERE id=N` → row gone, no evidence.
- Operator runs `UPDATE edit_history SET new_value='different'` → silent rewrite.

Realistic compromise scenarios:
- Maintainer cookie compromise + admin endpoints. Maintainer accounts cannot directly write SQL — they go through `edit.php` / `review.php` which append (don't modify) edit_history rows. The compromised maintainer COULD approve backdoored proposals that write new rows to edit_history, but cannot delete or rewrite existing rows via web. Audit trail survives.
- Shell compromise (separately tracked as out-of-scope here). At that point, ALL data on the host is tamperable.

So the realistic compromise threat to edit_history is: **shell-level breach of the prod host**. The audit trail can't defend itself from the OS user it runs under without an out-of-band sink.

### Decision menu (per plan)

| Option | Effort | Protection | Cost |
|---|---|---|---|
| **A. None (current)** | $0 | DB-level access trusts the operator + system. Honest for a single-operator hobby site. | $0/mo |
| **B. Append-only journal** | ~3-4 hr: PHP-side write to `~/logs/edit_audit.log` (operator-readable, php-fpm writable), file-mode constrained, no PHP code path to delete. | Partial — defeats a maintainer-cookie attacker AND a php-fpm-user attacker, but NOT a shell-level attacker (who can `rm`/`> file`). Detects post-hoc DB tampering by comparison. | $0/mo |
| **C. External sink (S3-compatible append-only bucket)** | ~1 day: PHP-side ship-on-write to e.g. Backblaze B2 / Hetzner Object Storage / Cloudflare R2 with object-lock-equivalent (write-once-read-many policy). | Real — survives prod-host compromise. Detects post-hoc DB tampering by external comparison. | ~$0.10-1/mo at this row volume |

### Recommendation

**Defer to a concrete trigger**, similar to D-T1.3:

- The current threat model is single-operator, hobby-grade, no compliance regime, ~1 incident per N years expected.
- The realistic concern is **post-incident forensics** ("did someone tamper with reach descriptions during the compromise window?") rather than active-attack prevention.
- For that specific use case, **the existing Hetzner storage-box backup + rclone offsite** (`docs/offsite-backup.md`) already provides a *daily-granularity* external snapshot of `edit_history`. Restoring a backup from before the incident and diffing the audit trail surfaces tampering. Not the same as cryptographic chain, but covers the realistic ask.

### Triggers to re-evaluate

- An incident occurs involving suspected `edit_history` tampering (or maintainer-account compromise).
- A second maintainer joins (same trigger as F-5, F-13).
- A compliance/audit requirement appears (unlikely for this site).
- The site grows beyond "hobby/club" tier of trust (e.g., commercial liability for misleading data).

### Phase 2.4 closeout

- ⚠ F-4 confirmed: no in-DB tamper-resistance.
- Decision options A/B/C laid out for D-T2.4 below.
- Existing backup infrastructure (per `docs/offsite-backup.md`) provides a *partial* external integrity check via daily snapshots — not equivalent to a hash-chained journal but adequate for the realistic post-incident forensics use case.

## Tier 2 closeout

### Audit summary

| Phase | Verdict | Findings touched |
|---|---|---|
| 2.1 Role enforcement | ✅ | none |
| 2.2 IDOR sweep | ✅ | none |
| 2.3 Privilege escalation | ✅ critical + 4 ⚠ refinements | F-7 (CLOSED Accepted), F-8 (stands), F-9 (downgraded), F-13 (confirmed) |
| 2.4 Audit trail integrity | ⚠ | F-4 (CLOSED Accepted via D-T2.4) |

### Decisions made during Tier 2

- **D-T2.4** Audit trail tamper resistance → Option A (None) with re-evaluation triggers. Recorded in `decisions.md`.

### Findings touched

| Id | Before Tier 2 | After Tier 2 |
|---|---|---|
| F-4 | 🔴 Open | ⚪ Accepted (D-T2.4) |
| F-7 | 🔴 Open | ⚪ Accepted (Phase 2.3: confirmed safe via cross-file invariant) |
| F-8 | 🔴 Open | 🔴 Open (code-smell, not exploitable) |
| F-9 | 🔴 Open (Medium) | 🔴 Open (Low; refined to apply only to reach_class) |
| F-13 | 🔴 Open | 🔴 Open (tied to multi-maintainer trigger) |

No new findings during Tier 2.

### Tier 2 verification gate (per plan)

- ✅ Authorization matrix documented (10 endpoints × 4 auth tiers).
- ✅ Each row tested.
- ✅ IDOR sweep produced no findings (Phase 2.2 verdict).
- ✅ D-T2.4 decision recorded.

### Looking ahead to Tier 3 (Input/output handling)

Tier 1 + Tier 2 surfaced findings now ripe for Tier 3:

- **F-6** htmlspecialchars ENT flags — Tier 3.1 XSS sweep.
- **F-8** SQL-concat code-smell refactor — fits naturally in a Tier 3.2 SQLi sweep where the cluster gets revisited.
- File-upload audit (Phase 3.3) — N/A until endpoint lands.

Tier 3 has 5 phases + decision point on file-upload retention.
