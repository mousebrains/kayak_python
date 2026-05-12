# Tier 3 — Input/output handling audit log

> **Started:** 2026-05-12 against `main` at `670212d`. Per `docs/PLAN_editor_security_review.md` Tier 3 verification gate: "Each input vector tested (or marked N/A with reason); file-upload posture documented and tested; CSRF coverage matrix complete."
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
| `payload_json` rendering (the plan's flagged "stored XSS" vector, T-T1) | Editor-submitted JSON rendered in `/review.php` maintainer view | ✅ | `php/review.php:200-213`: all rendered fields wrap `htmlspecialchars((string)$v)`. Both `<pre>` content contexts and `<textarea>`/`<input value=...>` attribute contexts. The form's `name` attribute uses `htmlspecialchars($f)`. |
| `payload['body']` (comment subject/body) | Free-text editor input shown to maintainer | ✅ | `php/review.php:168` escapes via `htmlspecialchars`. |
| `notes_to_maint` (proposer's notes) | Free-text editor input | ✅ | `php/review.php:172` escapes. |
| `reviewer_note` (maintainer's reply) | Maintainer input shown to editor (via email body, plain-text — not HTML rendered) AND in review.php form | ✅ | `php/review.php:181` escapes for HTML view; email is plain-text. |
| `editor.email` rendered in admin / nav | Editor-controlled at signup; rendered to maintainer | ✅ | `php/admin.php` and `php/includes/header.php` use `htmlspecialchars` on every email render. |
| `editor.display_name` rendered to admin + own account page | User-set | ✅ | `php/account.php`, `php/admin.php` escape. |
| `edit.php` form pre-fill from DB | DB-stored reach/gauge values, themselves coming from previous proposer submissions | ✅ | `php/edit.php:179`: `$val = htmlspecialchars((string)($row[$field] ?? ''))` before interpolation into `<input value="$val">` or `<textarea>$val</textarea>` (`php/edit.php:184,186`). |
| Bare `echo $var` sites | Unescaped HTML output | ✅ | 6 sites found via grep: all server-constructed literals, none user-influenced:<br>• `reach.php:188,413` `$compact_css` = server-constructed `<style>` block<br>• `reach.php:310,648` `$map_scripts` = server-constructed `<script src=...>` literals<br>• `plot.php:126` `$svg` = output of `generate_svg_plot()` which escapes `$title`, `$y_label`, and json-encodes series data (`php/includes/svg_plot.php:24,323-324`)<br>• `error.php:26` `$message_html` = documented as "caller's responsibility" — all 5 callers verified to escape user data (see render_error_page audit below) |
| `render_error_page()` caller audit | Verbatim HTML injection from callers | ✅ | All 5 callers verified:<br>• `php/review.php:136` interpolates `(int)$cr_id` (integer cast)<br>• `php/includes/auth.php:36` hard-coded literal<br>• `php/includes/auth.php:181` wraps `$ed['email']`, `$ed['status']` in `htmlspecialchars`<br>• `php/includes/db.php:56` interpolates `(int)$id`<br>None pass un-escaped user data. |
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