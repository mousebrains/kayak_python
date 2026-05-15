# Tier 6 — Hardening + closeout audit

> Per `docs/done/PLAN_editor_security_review.md` § Tier 6. Applies the findings + decisions from Tiers 1–5; produces a final posture summary.
>
> Cross-references: [findings.md](findings.md) for per-finding status; [decisions.md](decisions.md) for D-T1..5.x; [posture.md](posture.md) for the rolled-up summary; per-tier audit logs for the source analysis.

## Scope

What Tier 6 does:
1. **Applies remediations** for findings flagged as fixable in Tiers 1–3.
2. **Implements decisions** that produced concrete deliverables (CLIs + systemd timers + privacy.php refresh).
3. **Dispositions all remaining findings** to one of: Closed (fixed), Accepted (rationale + re-eval triggers documented), Deferred (trigger-bound to a specific event), or Open + prod-confirm (operator must verify on prod).
4. **Produces the final posture doc** (`posture.md`) so future reviewers can pick up the work.

What Tier 6 does NOT do:
- Re-audit prior tiers (covered by D-T5.3's re-review cadence).
- Make new decisions (those flow back to the relevant tier's decision section).
- Touch out-of-scope items (external pentest, WAF, etc., per the plan's "Out of scope").

## Tier 6.1 — Findings disposition

Per the verification gate, every finding must end Tier 6 with a clear disposition. Inventory at the end of Tier 6:

| Status | Count | Findings | Source |
|---|---|---|---|
| 🟢 Closed | 5 | F-1 (HSTS), F-2 (token in nginx log), F-14 (Referrer-Policy), F-15 (logout test), F-16 (privacy.php) | This tier |
| ⚪ Accepted | 6 | F-3 (alias normalization), F-4 (audit tamper resistance), F-5 (maintainer 2FA), F-6 (htmlspecialchars convention), F-7 (mass-assign whitelist), F-8 (SQL concat code-smell) | Tiers 1-3 + this tier |
| 🔵 Deferred | 2 | F-9 (over-tier reach_class apply), F-13 (self-approval prevention) — both trigger-bound to second-maintainer scenario | This tier |
| 🔴 Open | 3 | F-10, F-11, F-12 — prod-side confirmations the operator must run on prod | n/a (operator action) |

Closed findings produced code/config changes; see individual finding bodies for the commit references.

Accepted and Deferred findings all have explicit re-evaluation triggers documented. The 3 Open findings are NOT gaps to fix from the dev box — they require live prod-host shell access:

- **F-10:** `php-fpm -i 2>&1 | grep -E 'display_errors|expose_php'` on prod.
- **F-11:** `stat /var/log/nginx/kayak-error.log` on prod.
- **F-12:** `cat /etc/php/*/fpm/pool.d/*.conf | grep -E 'request_terminate_timeout|pm.max_children|pm.start_servers'` on prod.

Each Open finding lists the specific Repro command in its body.

## Tier 6.2 — Decision implementations

Per-decision implementation log:

| Decision | Implementation status | Code/config delta |
|---|---|---|
| D-T1.3 (magic-link only) | n/a — no code change | Existing behavior |
| D-T2.4 (no audit tamper-resistance) | n/a — relies on existing backups | n/a |
| D-T3.3 (file-upload retention) | n/a — deferred until upload endpoint exists | n/a |
| D-T4.1 (manual account deletion) | ✅ `levels delete-editor` CLI shipped Tier 6 | `src/kayak/cli/delete_editor.py` + main.py wiring + tests |
| D-T4.2 (on-request export) | ✅ `levels export-editor` CLI shipped Tier 6 | `src/kayak/cli/export_editor.py` + main.py wiring + tests |
| D-T4.3 (90-day retention purge) | ✅ `levels editor-retention` CLI + systemd timer shipped Tier 6 | `src/kayak/cli/editor_retention.py` + systemd units + sudoers + tests |
| D-T4.4 (refresh privacy.php) | ✅ "Your Rights" section rewritten; F-16 closed | `php/privacy.php` |
| D-T4.5 (security.txt minimum + annual refresh) | n/a — file unchanged; calendar item for 2027-04-01 | n/a (calendar) |
| D-T5.1 (security.txt only disclosure path) | n/a — same content as D-T4.5 | n/a |
| D-T5.2 (best-effort IR cadence) | n/a — documented in runbook | `docs/security/incident-response.md` |
| D-T5.3 (major-change + annual touch re-review cadence) | n/a — operational cadence; runs starting ~2027-05-12 | n/a (calendar) |

All decisions that produced code now have shipped implementations. Operational decisions (D-T5.x) are documented and either calendar-triggered or self-actuating.

## Tier 6.3 — Operator action list

Items that require operator-side execution on the prod host before Tier 6 is fully complete:

1. **Apply the deploy/* changes** (HSTS, log format) via:
   - `git pull` on the prod repo.
   - `sudo cp deploy/levels /etc/nginx/sites-available/levels`
   - `sudo cp deploy/kayak-log-format.conf /etc/nginx/conf.d/kayak-log-format.conf`
   - `sudo nginx -t && sudo systemctl reload nginx`
   - `sudo cp deploy/kayak-pipeline.sudoers /etc/sudoers.d/kayak-pipeline` (then `visudo -cf`)

2. **Install the new systemd units** via:
   - `sudo /home/pat/kayak/systemd/install.service.sh` (will pick up `kayak-editor-retention.{service,timer}` automatically since they're added to the install script).

3. **Run F-10 / F-11 / F-12 confirmations** (see commands above). Update findings.md with results (close as appropriate, file new findings if anything is off).

4. **Verify the F-1 / F-2 / F-14 fixes** live:
   - `curl -sI https://levels.wkcc.org/ | grep -i strict-transport` (F-1).
   - Complete a login, then `sudo tail /var/log/nginx/kayak-access.log` — Auth.php URL should show `?t=REDACTED` (F-2) and immediately following requests should show `-` in Referer (F-14).

5. **First backup-restore drill** (Phase 5.5) — operator's choice of when.

6. **Update findings.md** with the dispositions of F-10/11/12 once verified.

This action list is not Tier 6's blocker — Tier 6 ships the code/config. The operator runs the prod-side application at their convenience.

## Tier 6.4 — Posture doc

[posture.md](posture.md) summarizes the editor pipeline's end-of-tier-6 security posture for future reviewers. Includes:
- Quick-reference: what controls exist and where.
- Open obligations (re-evaluation triggers from Accepted + Deferred items).
- The operator's standing checklist (calendar items: annual security.txt refresh, annual light-touch re-review, annual backup-restore drill).
- Pointers to per-tier audit logs for deeper investigation.

## Tier 6 verification gate

- [x] Every finding has a Closed / Accepted / Deferred / Open-prod-confirm disposition (no "Open with no plan").
- [x] Every Active decision in decisions.md either has shipped code OR is genuinely n/a (documented as such above).
- [x] Operator action list captures the prod-side work that remains.
- [x] Posture doc (`posture.md`) exists.
- [x] README tier status updated.

**Tier 6 status: ✅ Complete from the dev-side. Operator action list (Tier 6.3) is the prod-side completion path.**
