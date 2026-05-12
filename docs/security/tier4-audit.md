# Tier 4 — User-data obligations audit

> Per `docs/PLAN_editor_security_review.md` § Tier 4. Five decision points (account deletion / data export / retention / privacy + ToS / security.txt). Unlike Tiers 1–3, this tier is almost entirely decisions — there is little to "test." The audit captures the current state, surfaces the menu, and records the rationale for each choice.
>
> Cross-references: [editor-surface.md](editor-surface.md) for the asset inventory, [findings.md](findings.md) for the resulting actions, [decisions.md](decisions.md) for the formal D-T4.x entries.

## Scope

The editor pipeline collects and retains the following user-affecting data:

| Table | Field(s) | Per-row PII | Lifetime today |
|---|---|---|---|
| `editor` | `email`, `display_name`, `request_note` | Identifier (email); optional name | Indefinite |
| `editor_session` | `ip`, `user_agent`, `token_hash` | Network identifier, browser fingerprint | Indefinite (rows survive past `expires_at`) |
| `editor_magic_link` | `ip_issued`, `next_url`, `token_hash` | Network identifier | Indefinite |
| `maintainer_credential` | WebAuthn public-key material | Cryptographic identity | Indefinite (none today) |
| `change_request` | `payload_json`, `notes_to_maint`, `source_url`, `subject` | User-supplied narrative content, sometimes identifying | Indefinite |
| `change_request_attachment` | `filename`, `caption`, file on disk | Potentially photo metadata (EXIF) | Indefinite (none today; no upload endpoint) |
| `edit_history` | `changed_by` (string `'editor:<id>'`), `old_value`, `new_value` | Indirect link to editor.id (durable past `editor` row delete because string FK is not enforced) | Indefinite |

Aux references:
- `editor.reviewed_by` → `editor.id` `ON DELETE SET NULL` (a reviewer whose account is deleted leaves NULL behind).
- `editor.id` FK chain: `editor_session.editor_id`, `editor_magic_link.editor_id`, `maintainer_credential.editor_id`, `change_request.editor_id` all `ON DELETE CASCADE`.
- `change_request.id` FK chain: `change_request_attachment.change_request_id CASCADE`; `edit_history.change_request_id SET NULL`.
- `edit_history.changed_by` is a free string (`String(64)`) not a true FK, so deleting `editor` does NOT alter the audit string. This is a deliberate audit-trail-preservation choice.

## Phase 4.1 — Account deletion

### Current state

- **No web-side deletion endpoint exists.** `php/account.php` allows display-name edit and self-ban (`status='banned'`) but no row delete.
- **No CLI deletion command exists.** `levels seed-maintainer` creates/promotes; nothing reverses.
- **Privacy policy commits** to "You can request deletion at any time by contacting the club" (`php/privacy.php:39-40`).
- **Cascade behavior** (verified above): `DELETE FROM editor WHERE id=?` cleans `editor_session` + `editor_magic_link` + `maintainer_credential` + `change_request` (and through change_request, `change_request_attachment` and SET-NULL on `edit_history.change_request_id`). `edit_history.changed_by` strings survive intentionally.
- **Effective demand:** zero so far (no requests received per operator). Realistic volume for a hobby/club site: < 1/year.

### Options

| Option | Effort | Implementation surface | Risk |
|---|---|---|---|
| A. Manual operator-handled (`levels delete-editor --email …`) | ~1 hour to write + test the CLI | New CLI module + per-table cleanup script; documented in `docs/operations.md` | Low. Operator-mediated; auditable via shell history |
| B. Self-serve deactivation (web button → set `status='banned'`, revoke sessions) | ~30 min — `account.php` already has self-ban UX skeleton | One handler in `account.php`; no schema change | Low; but does NOT satisfy "delete my data" — data is retained |
| C. Self-serve hard delete (web button → cascade delete) | ~2-3 hours including a confirmation UI, CSRF, audit-log entry of the deletion event, undo window | `account.php` handler + `edit_history` "deleted by self" marker + email confirmation flow | Higher. UI gives users a button that destroys data irreversibly; requires careful confirmation UX. |

### Recommendation

**Option A (Manual)**.

Rationale:
1. **Demand is theoretical.** Zero requests in the project's history; realistic volume is < 1/year for a hobby/club site.
2. **Privacy-policy commitment is satisfied** — "contact the club" is the documented path; an operator-side CLI is the operational backbone of that path.
3. **Self-serve UI for an unused feature is wasted effort** at this scale. Option C in particular adds a destructive-action UI element whose primary failure mode (accidental click) outweighs its benefit (instant gratification for a request volume that doesn't exist).
4. **Cascade behavior already supports** the manual path — no schema change needed, just a CLI wrapper that runs the DELETE in a transaction and prints the affected counts.

### Implementation (Tier 6)

When activating: add `src/kayak/cli/delete_editor.py` mirroring `seed_maintainer.py`:
- `--email <addr>` required.
- Print affected row counts (sessions, magic links, change_requests, attachments) and require `--yes` confirmation.
- Optionally `--anonymize-history` flag: UPDATE `edit_history` SET `changed_by` = `'deleted:<id>'` WHERE `changed_by` = `'editor:<id>'` OR `'maintainer:<id>'`. Default OFF (preserve audit attribution).
- DELETE FROM editor WHERE id=? inside a single transaction.

### Re-evaluation triggers

- Deletion-request volume hits ≥ 1/month (then consider Option B or C).
- A regulator imposes "self-serve right to be forgotten" obligations on the site.
- The site grows beyond hobby/club tier.

## Phase 4.2 — Data export

### Current state

- **No export endpoint exists.** No CLI; no web button.
- **Privacy policy does NOT explicitly promise export.** "Your Rights" section is silent on portability.
- **Effective demand:** zero.

### Options

| Option | Effort | Implementation surface |
|---|---|---|
| A. No export | 0 | n/a |
| B. On-request export | ~1 hour CLI: `levels export-editor --email …` → JSON to stdout | New CLI; reuses SELECT queries |
| C. Self-serve export | ~3-4 hours: web handler + JSON streaming + CSRF + rate-limit | `account.php` button + new handler |

### Recommendation

**Option B (On-request CLI)** as a paired complement to D-T4.1's manual deletion path.

Rationale:
1. **Pairs with deletion.** If a user requests deletion, the same operator session can produce the export first if asked. Same workflow, same constraints.
2. **Trivial to implement** as a SELECT across editor + change_request + edit_history filtered by editor_id, dumped as JSON.
3. **Self-serve (C) adds rate-limit / abuse surface** for a feature that has no current demand.
4. **No-export (A) is also defensible** but the CLI version is cheap insurance — having the script ready means a user request gets a same-day response.

### Implementation (Tier 6)

`src/kayak/cli/export_editor.py`:
- `--email <addr>` required.
- Output: JSON to stdout (or `--out <file>`) with structure: `{editor: {…}, change_requests: [{…}], edit_history: [{…}]}`.
- No payload_json schema introspection — emit raw for completeness.

### Re-evaluation triggers

- First user request → operator's experience informs whether to elevate to Option C.
- ≥ 1 export request/month sustained.
- Regulatory obligation (GDPR-equivalent).

## Phase 4.3 — Retention of audit/PII tables

Three sub-decisions, one per field family:

### 4.3a — `edit_history.changed_by` (audit attribution)

| Option | Description |
|---|---|
| A. Indefinite (current) | Permanently identifies who changed what |
| B. Anonymize after N years (e.g., `UPDATE … SET changed_by='deleted:<id>'` where `changed_at < now - N years`) | Audit shows the *what* and *when* indefinitely, but the *who* fades after N years |

**Recommendation: A (Indefinite).**
Rationale: edit_history is a content audit, not a PII log. The link `editor:42` resolves to PII only via the live `editor` table; deleting an editor (per D-T4.1) is what severs the PII linkage. Anonymizing changed_by independently of editor-row lifecycle would weaken audit value without strengthening privacy. The privacy gain happens at deletion time, not at retention horizon.

### 4.3b — `editor_magic_link.ip_issued`

Magic-link rows persist forever today (no cleanup). Each row holds `ip_issued` (the requester's IP at issuance). After expiry + 30 days, the row has no operational value — the link is dead, the token_hash is stale, and the IP is dead-weight PII.

| Option | Description |
|---|---|
| A. Indefinite (current) | Keeps every magic-link issuance forever |
| B. Purge rows where `expires_at < now - 90 days` (daily) | Aligns with `kayak-decimate` cadence; 90 days = enough for incident-window forensic look-back |
| C. Aggressive purge: purge as soon as `used_at IS NOT NULL OR expires_at < now` | Removes ALL forensic value immediately on consumption |

**Recommendation: B (90-day purge).**
Rationale: 90 days is the conventional "incident discovery window" for credential issuance logs. Operator notices something off → has ~3 months of issuance history to correlate. Aggressive purge (C) destroys the post-incident look-back. Indefinite (A) is needless PII accumulation.

### 4.3c — `editor_session.ip` + `editor_session.user_agent`

Same shape as 4.3b but for the session record (live for 7 days, then dead). Session row itself must survive past expiry so `current_editor()` can definitively reject (vs "not found" → silent fail).

| Option | Description |
|---|---|
| A. Indefinite (current) | Keeps every session row + IP/UA forever |
| B. Nullify ip+user_agent (NOT delete row) where `expires_at < now - 90 days` | Row survives for FK integrity / audit; PII drops out |
| C. Delete the row outright where `expires_at < now - 90 days` | Simpler; FK-clean (no enforced reference from elsewhere) |

**Recommendation: C (delete the row after 90 days past expiry).**
Rationale: confirmed via schema review that no other table references `editor_session.id`. Deleting the row is the simplest implementation. 90-day window again gives incident-window forensic value. (`revoked_at`-based "session forensics for a specific suspicious editor" use case is preserved within the 7-day live window + 90-day tail = ~97 days total.)

### Combined implementation (Tier 6)

New CLI: `src/kayak/cli/editor_retention.py` runs both purges:
```
DELETE FROM editor_magic_link WHERE expires_at < datetime('now', '-90 days');
DELETE FROM editor_session    WHERE expires_at < datetime('now', '-90 days');
```
Wire as `levels editor-retention`; add a systemd timer `kayak-editor-retention.timer` (daily, same shape as `kayak-decimate.timer`).

### Re-evaluation triggers

- An incident requires retroactive credential-issuance forensics beyond 90 days → consider extending to 180 or 365.
- Storage growth from these tables is not a current concern (each row is small) but if scale changes, the retention window can shorten.

## Phase 4.4 — Privacy policy + ToS

### Current state

`php/privacy.php` exists (4.7 KB, last-updated 2026-05-01). Sections present:
- Data We Collect (server logs, cookies, contributor email, proposed edits, no analytics)
- Login Email and Bot Protection (Google mail relay, Cloudflare Turnstile)
- How We Use Server Logs
- Third-Party Services (OSM, OpenTopoMap, Esri tiles, Leaflet)
- Data Sources (USGS et al — public data)
- Children's Privacy
- Your Rights
- Changes to This Policy

### Accuracy audit

Side-by-side reading of `php/privacy.php` against actual data collection:

| Privacy.php claim | Actual state | Verdict |
|---|---|---|
| "Browsing alone sets no cookies" | True for static-build pages; PHP pages may set `XDEBUG_SESSION` if debug enabled, but not in prod | ✓ Accurate |
| `ed_sess` + `ed_csrf` cookies, HttpOnly, SameSite=Strict, Secure on HTTPS, 7-day session | Verified in Tier 1.2 audit; matches | ✓ Accurate |
| "Contributor email address... You can request deletion at any time" | Currently no formal channel; commits to operator-handled response | ✓ Aligned with D-T4.1 (Option A) |
| "Proposed edits and comments... stored in our database for the maintainer to review" | Verified — `change_request` table | ✓ Accurate |
| "No analytics or tracking" | Verified — no GA, no FB pixel in templates | ✓ Accurate |
| "Login links are emailed via... Google's mail infrastructure" | Verified — postfix relays to gmail | ✓ Accurate |
| Turnstile mentioned | Verified — used on login + contact forms | ✓ Accurate |
| Children's Privacy: "does not knowingly collect any personal information from anyone, including children under 13" | Slight contradiction with the editor email collection above, but interpretable as "from anyone unknowingly" | ⚠ Mild tension; defensible |
| **"Your Rights"** section: **"Because we collect only server access logs and no personal data, there is generally no personal data to access, correct, or delete."** | **CONTRADICTS** the upper Data We Collect section which lists email, edits, comments | ✗ **STALE** — written before editor pipeline existed |

**New finding F-16** filed (see [findings.md](findings.md)).

### Options

| Option | Description | Effort |
|---|---|---|
| A. Keep current; add nothing | Accepts the F-16 contradiction | 0 |
| B. Refresh privacy.php — fix F-16, bump "Last updated", set annual review trigger | Targeted edit to "Your Rights" section | ~20 min |
| C. Add a brief Terms of Service page (/terms.php) | Sets contribution acceptance, content licensing, dispute handling expectations | ~1-2 hours |
| D. Lawyer-reviewed (privacy + ToS) | Formal legal review | Days + $$$ |

### Recommendation

**B (Refresh privacy.php to fix F-16).** Defer C (ToS) and D (lawyer).

Rationale:
1. **F-16 must be fixed regardless** — a privacy policy that contradicts itself is worse than no policy at all (it signals carelessness on a page that users read for trust signals).
2. **ToS is overkill at hobby-club scale.** No revenue, no commercial liability surface, no DMCA-takedown volume. The implicit terms (be reasonable, respect copyright, contributions licensed back to WKCC) are already conventional for club-operated open data; a written ToS adds maintenance burden without changing the relationship.
3. **Lawyer review** is appropriate only if revenue or specific compliance regimes (GDPR scope, COPPA, CCPA) become applicable.
4. **Annual review trigger** anchors the page to a calendar — paired with Tier 5's re-review cadence decision, the privacy page becomes a maintained artifact rather than a one-shot.

### Implementation (Tier 6)

Edit `php/privacy.php`:
- "Your Rights" section: replace with accurate paragraph (deletion via operator-handled path per D-T4.1, export via CLI per D-T4.2 on request, audit trail retention per D-T4.3, cookie expiry).
- Bump "Last updated: May 12, 2026" (or commit date).
- Add an HTML comment near the top: `<!-- Annual review trigger: next review 2027-05-12 -->`.

### Re-evaluation triggers

- New data class added to the editor pipeline (e.g., file uploads activate D-T3.3).
- Revenue stream introduced.
- Geographic expansion that brings new compliance regimes into scope.
- Annual review (calendar trigger).

## Phase 4.5 — security.txt

### Current state

`static/security.txt` (90 bytes):
```
Contact: mailto:pat.kayak@gmail.com
Expires: 2027-05-20T00:00:00Z
Preferred-Languages: en
```

Served via the nginx alias at `/.well-known/security.txt` (per `deploy/levels`).

### Options

| Option | Description | Effort |
|---|---|---|
| A. Keep current (Contact + Expires + Preferred-Languages) | RFC 9116 minimum + a courtesy | 0 |
| B. Add `Encryption: <url-to-pubkey.asc>` | Requires generating/maintaining a PGP key and hosting the .asc file | ~1 hour + ongoing key custody |
| C. Add `Acknowledgments: <url>` + `Policy: <url>` | Requires content at those URLs (a hall-of-fame page and an IR-cadence page) | Policy waits on Tier 5; Acknowledgments waits on first researcher |
| D. Refresh `Expires:` annually as part of Tier 5 re-review | Maintenance trigger; current expiry is 2027-05-20 | ~5 min annually |

### Recommendation

**A + D (Keep current minimum; add to annual maintenance calendar).**

Defer B (PGP) and C (Acknowledgments + Policy) until concrete trigger.

Rationale:
1. **Current minimum is RFC-compliant.** RFC 9116 requires only `Contact` and `Expires`; the `Preferred-Languages` line is a courtesy.
2. **PGP (B) presumes the reporter knows PGP.** In practice, security researchers at the hobby-site tier reach out via plain email; PGP adds friction without uptake. Adding PGP would also obligate the operator to maintain key custody (rotation, backup, revocation) — non-trivial.
3. **Acknowledgments (C) needs content.** An empty Acknowledgments page is worse than no link. Defer until the first researcher actually reports something disclosable.
4. **Policy URL (C) waits on Tier 5.** The Policy URL points to the IR cadence decision — until Tier 5 makes that decision, there's nothing to link to.
5. **Annual Expires refresh** is a calendar item. Current expiry 2027-05-20 → next refresh ~2027-04-01 (one month buffer). Pair with the Tier 5 re-review cadence.

### Implementation (Tier 6 / annual)

No file change today. Add a calendar reminder for 2027-04-01: "Refresh `static/security.txt` Expires line to 2028-05-20 (or +1 year from refresh date)." If Tier 5 picks a Policy URL or a researcher requests Acknowledgments, append at that time.

### Re-evaluation triggers

- A security researcher requests Acknowledgments.
- A security researcher requests PGP.
- Tier 5 decides an IR cadence + creates a Policy URL.
- Annual Expires refresh (2027-04-01).

## Audit observations

A1. **Cascade chain is well-designed for deletion.** All FK chains either CASCADE (sessions/links/credentials/proposals/attachments) or SET NULL (cross-editor `reviewed_by`; cross-CR `edit_history.change_request_id`). The one durable string-ID is `edit_history.changed_by` — that's a deliberate audit-preservation choice, not an oversight.

A2. **No retention policy is in place for any user-data table today.** Magic-link issuances accumulate forever; session rows survive past expiry forever. Volume is small (a few editors total) so storage is not pressing — but D-T4.3 codifies the intent.

A3. **`privacy.php` contradicts itself** in the "Your Rights" section — see F-16. Likely artifact of the privacy page predating the editor pipeline; was accurate at original write time.

A4. **The Cloudflare Turnstile dependency is disclosed** in privacy.php, but the conditions of Turnstile activation (when does the user "see" Turnstile vs. when is it invisible) are vague. Acceptable for a hobby site; not a finding.

A5. **No GDPR-specific framing.** Site is US-operated for a US-region club; no EU-resident traffic obligation today. If the site ever ships in EU CDNs or markets in EU languages, GDPR compliance becomes in-scope. Not a current finding.

## Decision summary (proposed for D-T4.x)

| # | Topic | Recommended option |
|---|---|---|
| D-T4.1 | Account deletion | **A** (Manual via `levels delete-editor` CLI in Tier 6) |
| D-T4.2 | Data export | **B** (On-request via `levels export-editor` CLI in Tier 6) |
| D-T4.3a | `edit_history.changed_by` retention | **A** (Indefinite; PII linkage broken at editor-row deletion time, not by audit-trail decay) |
| D-T4.3b | `editor_magic_link.ip_issued` retention | **B** (Purge rows where `expires_at < now - 90 days`) |
| D-T4.3c | `editor_session.ip` + `user_agent` retention | **C** (Delete rows where `expires_at < now - 90 days`) |
| D-T4.4 | Privacy + ToS | **B** (Refresh privacy.php to fix F-16; defer ToS) |
| D-T4.5 | security.txt | **A + D** (Keep current minimum; calendar Expires refresh 2027-04-01) |

## New findings

- **F-16** filed: Privacy policy "Your Rights" section is stale (predates the editor pipeline). See [findings.md](findings.md#f-16).

## Tier 4 verification gate

- [x] Each decision point has a written choice + rationale in [decisions.md](decisions.md) (D-T4.1 through D-T4.5).
- [x] Each chosen action is documented for Tier 6 implementation OR explicitly deferred (security.txt PGP/Policy/Acknowledgments deferred to triggers).
- [x] New findings filed (F-16).
- [x] Audit observations recorded.

**Tier 4 status: ✅ Complete.**
