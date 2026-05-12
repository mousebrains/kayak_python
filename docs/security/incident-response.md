# Incident response runbook — editor pipeline

> Maintained per Tier 5 of `docs/PLAN_editor_security_review.md`. Aligned with D-T5.1 (security.txt-only disclosure), D-T5.2 (best-effort response), D-T5.3 (major-change-triggered re-review).
>
> Audience: the operator (single-maintainer today; second maintainer would inherit this). Pre-incident, the operator should read this end-to-end once.

## Pre-incident dependencies

Make sure these are in place BEFORE an incident:

- **Gmail account 2FA** on `pat.kayak@gmail.com` is enabled (TOTP or hardware key). This is the only auth control on the maintainer account — see D-T1.3.
- **1Password access** for these vault items: "Kayak backup encryption (rclone crypt)", Gmail app password used by msmtp, Hetzner Cloud Console credentials, Cloudflare account credentials.
- **Hetzner abuse contact info known.** `abuse@hetzner.com` (or via Cloud Console → Support → Abuse). Required if a third-party tells you your IP is attacking them, or if you need Hetzner to null-route incoming traffic.
- **Backup chain confirmed working** within the last quarter (per [tier5-audit.md](tier5-audit.md) § Phase 5.5 drill).
- **An offline copy** of this runbook on a personal device. If the prod host is compromised, you can't trust your own checkout of this file from there.

## Response commitment (per D-T5.2)

**Best-effort.** Concretely:
- Security gmail (`pat.kayak@gmail.com`) is checked at least once per business day.
- Initial acknowledgment to a reporter: within 2 business days, more typically same-day.
- Triage decision: within 5 business days for non-urgent reports; same-day for "site is actively being abused" reports.
- Fix timeline: depends on severity (see severity matrix below).

If the reporter asks for an SLA stronger than this, redirect them to the Policy URL (if GHSA is later activated per D-T5.1).

## Discovery flows

### A. Reporter emails the security gmail

1. Reply within 2 business days: acknowledge receipt; ask clarifying questions if needed; share an expected next-step timeline.
2. Treat the report as untrusted until verified. Do not click links the reporter sends; read URLs first.
3. Reproduce locally (dev environment) before touching prod.
4. Proceed to **Triage**.

### B. Operator notices anomaly directly

Examples: nginx access log shows unusual `/auth.php?t=…` failure burst; `edit_history` shows changes the operator didn't make; the site is serving wrong content.

1. Capture the evidence immediately (screenshot / log tail to a separate file outside the prod host).
2. **Don't react in haste.** Confirm the anomaly is real — many "anomalies" turn out to be benign (botnet scanning attempting nonexistent endpoints, Cloudflare quirks, etc.).
3. Proceed to **Triage**.

### C. Out-of-hours / off-grid

If the operator is unavailable (vacation, no internet), best-effort means "as soon as the operator returns." There is no on-call rotation. This is the consequence of single-operator best-effort and is documented in D-T5.2.

## Triage

### Severity matrix

| Severity | Examples | Response window |
|---|---|---|
| **Critical** | Active credential theft observed in logs; arbitrary code execution; data exfiltration in progress; site defacement | Contain within hours of operator awareness |
| **High** | Exploitable auth bypass; SQL injection; XSS in a privileged context; leak of a credential (rclone password, Turnstile secret, etc.) | Contain within 1-2 days; patch within ~1 week |
| **Medium** | Information disclosure (non-credential); CSRF gap; rate-limit bypass; missing security header | Patch within 1 month; add to [findings.md](findings.md) |
| **Low** | Best-practice deviations; documentation gaps; theoretical issues with no demonstrated path | File in [findings.md](findings.md); address per normal cadence |

If unsure, treat as one level higher than first instinct.

### Triage questions to ask

For each report or observation:
1. **Is the issue REAL?** Reproduce in a non-prod environment if possible.
2. **Is it being actively exploited?** Check nginx access logs (`/var/log/nginx/kayak-access.log`), edit_history, and editor_session for anomalies.
3. **What's the blast radius?** Reach data (low — public anyway), editor PII (medium — emails), credentials (high), site availability (medium).
4. **Can it be contained without taking the site down?**
5. **Does it require user notification?** (See § User notification.)

## Containment

Pick the LEAST invasive option that addresses the active threat.

### C1 — Revoke all editor sessions

When: a session-cookie leak is suspected (e.g., XSS confirmed), or operator just wants a clean slate after credential rotation.

```bash
ssh kayak-prod
sqlite3 /home/pat/DB/kayak.db <<'SQL'
BEGIN;
UPDATE editor_session SET revoked_at = datetime('now') WHERE revoked_at IS NULL;
COMMIT;
SELECT changes();  -- print rows affected
SQL
```
Effect: every editor's `ed_sess` cookie becomes immediately invalid (per `php/includes/auth.php` `current_editor()` which filters `s.revoked_at IS NULL`). Editors must re-login via magic-link.

### C2 — Lock a specific editor

When: one editor's account is compromised, but the broader pipeline is fine.

```bash
sqlite3 /home/pat/DB/kayak.db <<'SQL'
BEGIN;
UPDATE editor SET status = 'banned' WHERE email = 'compromised@example.com';
UPDATE editor_session SET revoked_at = datetime('now')
  WHERE editor_id = (SELECT id FROM editor WHERE email = 'compromised@example.com');
COMMIT;
SQL
```
Effect: `current_editor()` rejects (filters `e.status != 'banned'`); all that editor's sessions are revoked.

### C3 — Take site read-only

When: write paths are being abused but read paths are fine.

Comment out the `location ~ \.php$` block's write-path entries OR add an `nginx` `return 503` to the editor endpoints in `deploy/levels`. Reload nginx. Static-build pages keep serving from `/home/pat/public_html/`.

Less surgical: stop PHP-FPM (`sudo systemctl stop php8.1-fpm`). All PHP returns 502; static pages still serve.

### C4 — Take site offline

When: active credential theft in progress; arbitrary code execution suspected; uncertain blast radius.

```bash
sudo systemctl stop nginx
```
Or use Cloudflare's "I'm under attack" mode if Cloudflare is in front. Site returns nothing until investigation completes.

### C5 — Hetzner null-route

When: the host itself is being DDoSed beyond `limit_req` capacity, or a botnet is exhausting connection budget.

Email `abuse@hetzner.com` from the Hetzner Cloud Console reference email; include the source IPs being null-routed if known. Hetzner can null-route to/from specific addresses or temporarily move IP-blocking upstream.

## Credential rotation playbooks

Per credential class. Rotate AT MINIMUM whenever a credential is suspected compromised; consider rotating opportunistically on any major incident.

### rclone crypt password (offsite backup encryption)

If compromised, the offsite-backup chain is at risk of decryption. Rotate carefully — losing both old and new passwords loses access to all backups.

1. Generate a new password (long random string).
2. Update `~/.config/rclone/rclone.conf` `[gdrive-crypt]` section's `password` / `password2`.
3. **Re-encrypt existing backups:** OR accept that old backups remain encrypted with the old password and store both passwords in 1Password. Re-encryption is `rclone copy old-remote new-remote` and is expensive at scale.
4. Update 1Password "Kayak backup encryption (rclone crypt)" with the new password (and a note about which backups are encrypted under which key).
5. Test: `rclone ls gdrive-crypt:` should still list.

### Turnstile secret (Cloudflare)

Per `deploy/secrets.env.example` and `deploy/kayak-fpm-pool.conf:43` — `TURNSTILE_SECRET` is injected into PHP-FPM env from `/etc/kayak/secrets.env`.

1. Cloudflare dashboard → Turnstile → site `levels.wkcc.org` → "Rotate secret."
2. Update `/etc/kayak/secrets.env` `TURNSTILE_SECRET=...` on the prod host.
3. `sudo systemctl reload php8.1-fpm`.
4. Test: submit `/contact.php` and `/login.php` to confirm captcha still works.

### Postfix / msmtp Gmail app password

Per `deploy/SETUP.md` § 17 — `/etc/msmtprc` holds the 16-char app password.

1. Google Account → Security → 2-Step Verification → App passwords. Revoke the old one. Generate a new one.
2. `sudo sed -i 's/OLD_APP_PASSWORD/NEW_APP_PASSWORD/' /etc/msmtprc`.
3. Test: `echo "test after rotation" | msmtp pat.kayak@gmail.com 2>&1 | tail -5`.

### SSH access to prod host

If the operator's SSH key is suspected compromised:
1. Generate a fresh keypair locally.
2. Add the new public key to `~/.ssh/authorized_keys` on prod (via Hetzner Cloud Console rescue mode if no SSH access).
3. Remove the old public key entry.
4. Test the new key works.
5. (Optional) Audit `~/.ssh/authorized_keys` for unexpected entries.

### Magic-link tokens

Magic-link tokens are sha256-hashed at rest (`editor_magic_link.token_hash`) with 30-min expiry; revocation = mass-invalidation:
```bash
sqlite3 /home/pat/DB/kayak.db "UPDATE editor_magic_link SET used_at = datetime('now') WHERE used_at IS NULL;"
```
Effect: every unconsumed magic-link is now considered used; users requesting login generate new links.

### Maintainer account password (none today)

There is no maintainer password — maintainer authenticates via magic-link to the same gmail address (per D-T1.3). The "rotation" path is to rotate the gmail account's password and 2FA settings via Google's account flow.

## User notification

### When to notify users

Notify users when:
- An editor PII leak is confirmed (email addresses or display names disclosed externally).
- Editor sessions were mass-revoked (so they know why login broke).
- A change to user-facing security posture was forced by an incident (e.g., 2FA newly required).

Do NOT proactively notify when:
- The incident was contained without user-facing impact.
- The "incident" turned out to be a non-incident.

### Notification template

Send from `pat.kayak@gmail.com`. Plain text. One per affected user; do NOT BCC mass.

```
Subject: levels.wkcc.org — security update affecting your account

Hi [name or "there"],

I'm writing because [BRIEF FACT — e.g., "your login session was
invalidated as part of a precaution this morning" or "your email
address was potentially exposed in an incident discovered on
2026-MM-DD"].

What happened:
[1-2 sentences. Plain language. No spin.]

What I did:
[1-2 sentences. Concrete actions taken to contain.]

What this means for you:
- [Action #1 — e.g., "Log in again at https://levels.wkcc.org/login.php"]
- [Action #2 — e.g., "If you reused your email password elsewhere, consider rotating it" — only if PII was exposed]

What I'm doing next:
[1-2 sentences. Honest about ongoing steps and follow-up.]

If you have questions, reply to this email. You can also reach the
Willamette Kayak and Canoe Club at https://wkcc.org.

— Pat
levels.wkcc.org operator
```

## Post-incident review

Within 2 weeks of incident closure:
1. Write a brief post-mortem to `docs/security/post-mortems/YYYY-MM-DD-<slug>.md` (create the directory at first use).
   - What happened.
   - Timeline (UTC; rough OK).
   - Containment actions taken (which from C1-C5 + which rotations).
   - Root cause analysis.
   - What worked; what didn't.
   - Concrete follow-ups → filed as new findings in `findings.md` if they're code-side gaps, or as updates to this runbook if procedural.
2. Re-evaluate any Active decision in `decisions.md` whose trigger fired during the incident.
3. Update this runbook if a step was missing or wrong.
4. Add a one-line entry to [tier5-audit.md](tier5-audit.md) § "Drill log" with the post-mortem path.

## Recovery from backup

Per `docs/db_sync.md` and `docs/offsite-backup.md`:
- `scripts/db_pull.sh` pulls a copy from prod (dev-side use).
- **NEVER** run `scripts/db_push.sh` on the prod host (per standing instruction `[feedback_never_run_db_push]`).
- **NEVER** overwrite live DB with a restored copy without explicit operator confirmation (per `[feedback_never_overwrite_db]`).

If a restore IS the right answer (live DB is poisoned beyond surgical repair):
1. Stop the site (C4 above).
2. Take a forensic copy of the current poisoned DB to `~/kayak.db.poisoned-YYYY-MM-DD` (do NOT delete; needed for post-incident analysis).
3. Pull the most recent backup that pre-dates the compromise; verify integrity per [tier5-audit.md](tier5-audit.md) § Phase 5.5.
4. Manually `cp` into `/home/pat/DB/kayak.db` (atomic rename: `cp restore.db /home/pat/DB/kayak.db.new && mv /home/pat/DB/kayak.db.new /home/pat/DB/kayak.db`).
5. Restart services (`sudo systemctl start nginx`; PHP-FPM picks up the new DB).
6. Reconcile the gap: changes made between snapshot date and incident must be manually re-applied where appropriate; document what's NOT being re-applied in the post-mortem.

## Lessons-learned hook

When a major change triggers re-review per D-T5.3, also re-read this runbook for staleness. Common drift:
- New endpoint added → containment step for that endpoint may need to be added.
- New credential introduced → rotation playbook missing.
- New external dependency → "pre-incident dependencies" list needs update.
