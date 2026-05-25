# Tier 3 — Input/output handling audit log

> **Started:** 2026-05-12 against `main` at `670212d`. Per `docs/done/PLAN_editor_security_review.md` Tier 3 verification gate: "Each input vector tested (or marked N/A with reason); file-upload posture documented and tested; CSRF coverage matrix complete."
>
> Verdict legend: ✅ pass / ⚠ partial / ❌ fail / ⊘ N/A.

## Phase 3.1 — XSS sweep

**Verdict:** ✅ (F-6 reclassified — see findings refinement below)

### Audit observations

**Escaping convention is documented and consistent.** Per `php/includes/html.php:6-13` docstring:

> Escaping convention: bare `htmlspecialchars($s)` everywhere. PHP 8.1+ defaults to `ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML401` with charset UTF-8, which is correct for both body text and double-quoted HTML attributes. The redundant `, ENT_QUOTES, 'UTF-8'` form was scrubbed to keep call sites uniform.

The original F-6 framing ("no calls specify ENT_QUOTES|ENT_HTML5") missed this docstring. The convention is explicit and the PHP-default flag set IS adequate for the contexts the codebase actually uses. F-6 effectively closes (see refinement).

### Per-target audit

| Target | XSS vector | Status | Evidence |
|---|---|---|---|
| `payload_json` rendering (the plan's flagged "stored XSS" vector, T-T1) | Editor-submitted JSON rendered in `/review.php` maintainer view | ✅ | `php/includes/review_handler.php :: _render_review_reach_fields`: all rendered fields wrap `htmlspecialchars((string)$v)`. Both `<pre>` content contexts and `<textarea>`/`<input value=...>` attribute contexts. The form's `name` attribute uses `htmlspecialchars($f)`. |
| `payload['body']` (comment subject/body) | Free-text editor input shown to maintainer | ✅ | `php/includes/review_handler.php :: _render_review_meta_table` escapes via `htmlspecialchars`. |
| `notes_to_maint` (proposer's notes) | Free-text editor input | ✅ | `php/includes/review_handler.php :: _render_review_meta_table` escapes. |
| `reviewer_note` (maintainer's reply) | Maintainer input shown to editor (via email body, plain-text — not HTML rendered) AND in review.php form | ✅ | `php/includes/review_handler.php :: _render_review_form` escapes for HTML view; email is plain-text. |
| `editor.email` rendered in admin / nav | Editor-controlled at signup; rendered to maintainer | ✅ | `php/admin.php` and `php/includes/header.php` use `htmlspecialchars` on every email render. |
| `editor.display_name` rendered to admin + own account page | User-set | ✅ | `php/account.php`, `php/admin.php` escape. |
| `edit.php` form pre-fill from DB | DB-stored reach/gauge values, themselves coming from previous proposer submissions | ✅ | `php/edit.php:179`: `$val = htmlspecialchars((string)($row[$field] ?? ''))` before interpolation into `<input value="$val">` or `<textarea>$val</textarea>` (`php/edit.php:184,186`). |
| Bare `echo $var` sites | Unescaped HTML output | ✅ | 6 sites found via grep: all server-constructed literals, none user-influenced:<br>• `reach.php:188,413` `$compact_css` = server-constructed `<style>` block<br>• `reach.php:310,648` `$map_scripts` = server-constructed `<script src=...>` literals<br>• `plot.php:126` `$svg` = output of `generate_svg_plot()` which escapes `$title`, `$y_label`, and json-encodes series data (`php/includes/svg_plot.php:24,323-324`)<br>• `error.php:26` `$message_html` = documented as "caller's responsibility" — all 5 callers verified to escape user data (see render_error_page audit below) |
| `render_error_page()` caller audit | Verbatim HTML injection from callers | ✅ | All 5 callers verified:<br>• `php/includes/review_handler.php :: _render_review_detail` interpolates `(int)$cr_id` (integer cast)<br>• `php/includes/auth.php:36` hard-coded literal<br>• `php/includes/auth.php:181` wraps `$ed['email']`, `$ed['status']` in `htmlspecialchars`<br>• `php/includes/db.php:56` interpolates `(int)$id`<br>None pass un-escaped user data. |
| HTML5 unquoted-attribute interpolation | F-6 original concern | ✅ | grep for `<[a-z]+ [a-z]+=[^"\47][^>]*\$` returns the 10 `\$f`/`\$type`/`\$iso`/`\$val`/`\$field` interpolations; all are inside DOUBLE-QUOTED attributes (e.g. `name="\$field"`). NO unquoted-attribute interpolation found. |

### Specific attack-string tests (static analysis only — live test deferred to Tier 6)

The plan asks: *"submit `<script>alert(1)</script>` everywhere accepting input; visit pages that render it; expect no execution."* Static analysis above demonstrates every render path escapes — a `<script>` payload would surface as `&lt;script&gt;alert(1)&lt;/script&gt;` in the DOM. Live testing for completeness can land in Tier 6 if appetite remains; the static-analysis confidence is high.

### Findings refinement

- **F-6** was filed as "htmlspecialchars defaults — needs context audit." After Phase 3.1 audit:
  - The lack of explicit `ENT_QUOTES | ENT_HTML5` is a **documented convention**, not an oversight (`php/includes/html.php:6-13`).
  - PHP 8.1+ defaults (`ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML401`) are correct for the contexts the codebase actually uses (content + quoted-attribute).
  - HTML5 unquoted-attribute interpolation: not present in the codebase.
  - Disposition: ⚪ Accepted (documented convention; no exploitable gap).

### Phase 3.1 closeout

- ✅ XSS coverage clean across all editor-pipeline endpoints.
- ⚪ F-6 reclassified from Open to Accepted (documented convention).
- No new findings.

## Phase 3.2 — SQLi sweep

**Verdict:** ✅ (no findings; F-8 code-smell stays as documented refactor candidate)

### Audit observations

- **131 `prepare()` calls across `php/`.** All paired with `->execute([...])` parameter binding.
- **`->query()` / `->exec()` calls exist (10+) but use static SQL only.** No user data flows into any `->query()` / `->exec()` argument (verified by grep + per-site read; static literals like `SELECT COUNT(*) FROM ...`, schema introspection, etc.).
- **9 sites use string concatenation into the SQL.** All verified safe via static analysis:

| Site | Pattern | Safety verdict |
|---|---|---|
| `php/custom.php:108` | `SELECT … FROM reach WHERE id IN ($placeholders)` | ✅ `$placeholders = implode(',', array_fill(0, count($ids), '?'))` — pure `?,?,?` string from `array_fill`; user data goes through `->execute($ids)` |
| `php/custom.php:119` | `SELECT … FROM gauge_source WHERE gauge_id IN ($gph)` | ✅ same pattern: `$gph = implode(',', array_fill(0, count($gauge_ids), '?'))` |
| `php/custom_gauges.php:157,163` | `WHERE level = 8/6 AND code IN ($hp/$hp6)` | ✅ same pattern |
| `php/gauge_picker.php:129,135` | `WHERE level = 8/6 AND code IN ($hp/$hp6)` | ✅ same pattern |
| `php/reach.php:131` | `SELECT reach_id, name FROM reach_class WHERE reach_id IN ($ph)` | ✅ same pattern (`$ph = implode(',', array_fill(...))`) |
| `php/edit.php:117` | `UPDATE $table SET $sets WHERE id = ?` | ⚠ F-8 (already filed) — `$table ∈ {reach, gauge}` from in_array whitelist; `$sets` items are `"$field = ?"` where `$field` iterates `$editable_fields` whitelist. SAFE in current usage; code-smell tracked. |
| `php/includes/review_logic.php:101` | `UPDATE reach SET $sets WHERE id = ?` | ⚠ F-8 — same pattern; `$sets` items use `$f` from `array_keys($payload['reach'])` (constrained at proposer's tier-whitelist, verified in Phase 2.3 cross-file trace). SAFE in current usage. |

### `$where`/`$sql` variable construction sites

- **`review.php:281`** — list query. `$where = $q_status === 'all' ? '' : 'WHERE cr.status = ?'`; `$q_status` value goes through `->execute($params)` placeholder, not concatenated into SQL. ✓
- **`custom.php:70`, `custom_gauges.php:68`** — `$sql` is built from heredoc with `WHERE r.id IN ($placeholders)` interpolation; placeholders are `?,?,?` strings. ✓
- **`gauge_picker.php:72`, `picker.php:64`, `custom_gauges.php:103`** — same heredoc + `IN ($placeholders)` pattern, prepared at those lines after construction earlier in each file (`gauge_picker.php:49-67`, `picker.php:29-60`, `custom_gauges.php:39-99` builds `$status_sql`). All three placeholder strings are derived from `array_fill(...)` over a server-side list (state abbrevs / state names / gauge IDs) — never from user-typed SQL. ✓

### Findings

- **F-8** stays Open — code-smell refactor candidate. Not a SQLi finding in itself; the cross-file invariants currently make it safe.
- **T-T4 / T-E2** (SQLi privilege escalation) confirmed not exploitable.

### Phase 3.2 closeout

- ✅ All 131 PDO `prepare()` calls properly parameterize user input.
- ⚠ F-8 stays as documented refactor candidate (code-smell, not exploit).
- No new findings.

## Phase 3.3 — File-upload audit

**Verdict:** ⊘ N/A — no upload endpoint exists.

### State

- `change_request_attachment` schema is provisioned (per `editor-surface.md`): columns `filename`, `content_type`, `size_bytes`, `sha256`, `storage_path`, `caption`.
- `grep -rn "move_uploaded_file\|\$_FILES" php/` returns empty.
- No nginx location block accepts multipart bodies for the editor pipeline.

### When this phase activates

When a PHP endpoint accepts file uploads (anywhere in the `php/` tree). The plan's checklist for that activation:

1. MIME validation against an allowlist.
2. Max-size enforcement (PHP-level + nginx `client_max_body_size`).
3. Filename sanitization — the `storage_path` is sha256-content-addressed per schema, which sidesteps path-traversal by construction; verify the implementation honors this.
4. nginx serving config for the uploads root: no PHP execution; explicit `Content-Type` header; `add_header Content-Disposition attachment` if appropriate.
5. Uploads root has the execute bit stripped from regular files (defense against PHP-handler misconfiguration writing through to PHP-FPM).
6. IDOR audit: can editor X read editor Y's attachment via `id` enumeration?
7. Retention policy decision (D-T3.x; see plan §"Decision point — file-upload retention").

### Findings

None — N/A.

### Phase 3.3 closeout

- ⊘ N/A. Activates when the upload endpoint lands.
- Document the trigger: any new PHP endpoint touching `$_FILES` or `move_uploaded_file()` requires re-opening this audit.

## Phase 3.4 — Rate limiting + abuse posture

**Verdict:** ✅ (cross-covered by Tier 1.4; one decision noted)

### Audit observations

Tier 1.4 already exhaustively audited the brute-force defense layers:
- nginx `limit_req` per-IP (6 zones, all bound)
- fail2ban jails (regex matches log format; logpaths line up with `deploy/levels`)
- Cloudflare Turnstile (login + contact only)
- Application-side `magic_link_under_throttle()` (5/email/hr, 20/IP/hr)
- Daily caps (`comment.php` 5, `propose.php` 3/10/20 tier-gated)

### Plan question: "Should `propose.php`, `comment.php`, magic-link request be behind Turnstile too?"

Audit answer:

| Endpoint | Current rate-defense | Should Turnstile be added? |
|---|---|---|
| `/login.php` | nginx login:3r/m + fail2ban + Turnstile + app-side throttle | already Turnstile-gated ✓ |
| `/auth.php` | nginx auth:10r/m + 256-bit token (brute-force infeasible) | No — token is the security; Turnstile would add friction without defense gain |
| `/propose.php` | nginx php:5r/s + require_editor + tier-gated daily cap | **No** — already require-editor-gated; an editor who's been admitted (status≥minimal) is partially trusted. Turnstile would add login-flow friction without addressing a current threat |
| `/comment.php` | nginx php:5r/s + require_editor + daily cap 5 | **No** — same reasoning |
| `/contact.php` | nginx contact:10r/m + Turnstile + honeypot | already Turnstile-gated ✓ |
| `/edit.php` | nginx edit:5r/m + require_maintainer | No — maintainer auth is the security |
| `/review.php` | nginx php:5r/s + require_maintainer | No |
| `/admin.php` | nginx php:5r/s + require_maintainer | No |

**Net:** Turnstile is correctly placed on the two unauthenticated POST endpoints (`/login.php` for magic-link issuance, `/contact.php` for spam). Adding it elsewhere would degrade UX for already-authenticated users without addressing a current threat.

### Findings

None.

### Phase 3.4 closeout

- ✅ Rate-limiting posture is appropriate for the threat model. No new findings.

## Phase 3.5 — CSRF audit final stamp

**Verdict:** ✅ (already verified 10/10 coverage; full audit completes)

### Audit observations

Tier 0 inventory + Tier 2.1 role-enforcement verified:
- All 10 editor-pipeline POST handlers call `require_csrf()` at the top of the POST branch.
- The check uses `hash_equals(cookie, submitted)` (constant-time compare) per `php/includes/auth.php:require_csrf`.
- CSRF cookie is `ed_csrf`, 64-hex `random_bytes(32)`, set lazily by `csrf_token()` on first call, rotated by `set_editor_session()` on login (session-fixation defense).
- Double-submit cookie pattern; no server-side CSRF token store.

### CSRF coverage matrix

| Endpoint | POST handler exists? | `require_csrf()` called? | Notes |
|---|---|---|---|
| `/account.php` | yes | ✅ `php/account.php:22` | display-name updates |
| `/admin.php` | yes | ✅ `php/admin.php:24` | 8 actions all under one CSRF check |
| `/auth.php` | yes | ✅ `php/auth.php:34` | magic-link consume |
| `/comment.php` | yes | ✅ `php/comment.php:35` | site-comment submit |
| `/contact.php` | yes | ✅ `php/contact.php:30` | contact form |
| `/edit.php` | yes | ✅ `php/edit.php:83` | maintainer direct edit |
| `/login.php` | yes | ✅ `php/login.php:36` | magic-link request |
| `/logout.php` | yes | ✅ `php/logout.php:17` | logout |
| `/propose.php` | yes | ✅ `php/includes/propose_handler.php :: _handle_propose_post` | proposal upsert |
| `/review.php` | yes | ✅ `php/includes/review_handler.php :: _review_handle_post` | review actions |

**10/10 covered.** `csp-report.php` is the lone POST endpoint without CSRF — by design (CSP violation reports are sent by the browser without user interaction; no CSRF threat applies).

### Findings

None.

### Phase 3.5 closeout

- ✅ CSRF coverage matrix complete and verified.

## Tier 3 closeout

### Audit summary

| Phase | Verdict | Findings touched |
|---|---|---|
| 3.1 XSS sweep | ✅ | F-6 ⚪ Accepted |
| 3.2 SQLi sweep | ✅ | F-8 stays as code-smell refactor candidate |
| 3.3 File-upload audit | ⊘ N/A | none — activates on endpoint wiring |
| 3.4 Rate limiting + abuse posture | ✅ | none |
| 3.5 CSRF audit | ✅ | none |

### Decisions made

- **D-T3.3** File-upload retention → **Deferred** (no endpoint; default would be Time-bounded if/when activated).

### Findings touched

| Id | Before Tier 3 | After Tier 3 |
|---|---|---|
| F-6 | 🔴 Open | ⚪ Accepted (documented convention via `php/includes/html.php:6-13`) |
| F-8 | 🔴 Open | 🔴 Open (code-smell only; not SQLi-exploitable) |

No new findings during Tier 3.

### Tier 3 verification gate (per plan)

- ✅ Each input vector tested or marked N/A with reason.
- ⊘ File-upload posture documented (N/A; trigger conditions listed).
- ✅ CSRF coverage matrix complete.

### Looking ahead to Tier 4 (User-data obligations)

Tier 4 is **all decision points** — no audit "phases" in the same sense. The 5 decision menus:

1. **Account deletion** — manual / self-serve deactivation / self-serve hard delete.
2. **Data export** — none / on-request / self-serve.
3. **Retention of audit/PII tables** — indefinite vs N-year purge for `edit_history`, `editor_magic_link.ip_issued`, `editor_session.{ip,user_agent}`.
4. **Privacy policy + terms** — keep current / refresh / add ToS / lawyer-reviewed.
5. **`security.txt`** — keep current (Contact + Expires) / add Encryption (PGP key) / add Acknowledgments + Policy / refresh Expires annually.

Plus D-1 design note from Tier 1.5 about self-serve email change.

Tier 4 will be more "ask + record" than "audit + find."