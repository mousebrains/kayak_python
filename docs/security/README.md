# `docs/security/`

Security artifacts for the editor pipeline. Maintained per `docs/done/PLAN_editor_security_review.md`.

> **Config-path note (2026-05):** references in these docs to `deploy/levels` are
> to the pre-split monolithic nginx vhost, which was split into `conf/sites/*` +
> `conf/snippets/levels-common.conf` + `conf/security-headers.conf` (HSTS) and
> removed. Line numbers like `deploy/levels:329` are point-in-time audit
> evidence; the live directives now live in those files.

## Contents

| File | Purpose | Updated when |
|---|---|---|
| [editor-surface.md](editor-surface.md) | Inventory: 10 PHP entry points, 7 DB tables (with PII/credential tags), cookies, rate limits, external services, logging surface. The "what exists" source of truth. | A new editor endpoint, table, cookie, rate limit, or external service joins the pipeline. |
| [threat-model.md](threat-model.md) | STRIDE-style threat enumeration (29 threats, 3 actor profiles). The "what could go wrong" reference. | A new threat surfaces (e.g. a new attack technique applicable to existing assets), or a new asset class joins the surface. |
| [controls-map.md](controls-map.md) | Threat → control mapping with `file:line` refs and ✓/⚠/✗/⊘ status. The "what defends us" reference. | A control changes (added / removed / weakened) or a threat is revised. |
| [findings.md](findings.md) | Open gaps + decisions, with per-finding remediation options + plan-tier allocation. The action tracker. | A finding is filed / dispositioned / closed. |
| [decisions.md](decisions.md) | Per-tier decision-point log. Each entry: chosen option, rationale, re-evaluation triggers. | A decision is made at the end of a tier. |
| [tier1-audit.md](tier1-audit.md) | Tier 1 (Authentication) audit log — per-phase test results, pass/fail verdicts, mitigation effort. | Per-phase during Tier 1; final closeout summary at end. |
| [tier2-audit.md](tier2-audit.md) | Tier 2 (Authorization) audit log — same shape; covers role enforcement, IDOR, privilege escalation, audit trail integrity. | Per-phase during Tier 2; final closeout. |
| [tier3-audit.md](tier3-audit.md) | Tier 3 (Input/output) audit log — XSS, SQLi, file-upload (N/A), rate-limit posture, CSRF. | Per-phase during Tier 3; final closeout. |
| [tier4-audit.md](tier4-audit.md) | Tier 4 (User-data obligations) audit log — account deletion, data export, retention, privacy/ToS, security.txt. | Per-decision during Tier 4; final closeout. |
| [tier5-audit.md](tier5-audit.md) | Tier 5 (Disclosure + response) audit log — disclosure path, IR cadence, re-review cadence, restore-drill plan, drill log. | Per-decision during Tier 5; per-drill log append. |
| [incident-response.md](incident-response.md) | Operator's IR runbook — pre-incident dependencies, discovery flows, triage matrix, containment options (C1-C5), credential rotation playbooks, user notification template, post-incident review template. | After every incident; whenever D-T5.3 triggers a re-review. |
| [tier6-audit.md](tier6-audit.md) | Tier 6 (Hardening + closeout) audit log — per-finding disposition, per-decision implementation status, operator action list. | When the review actually closes (one-shot). |
| [posture.md](posture.md) | Rolled-up end-of-review posture snapshot — controls inventory, accepted-findings triggers, operator's standing checklist. | Major change per D-T5.3, annual light-touch re-review, when a finding is filed/closed/dispositioned. |

## Reading order

For someone new to this work:

1. **`editor-surface.md`** to understand the asset map.
2. **`threat-model.md`** to understand the risk model.
3. **`controls-map.md`** to see what's already defending vs. what's not.
4. **`findings.md`** to see the action list.

For someone returning to file a new finding:

- Jump to [findings.md](findings.md), follow the existing entry format, cross-ref `T-Xn` from threat-model.md and `[F-N]` from controls-map.md.

For someone returning to update controls after a code change:

- Update [controls-map.md](controls-map.md) row for the affected threat(s). If the change closes a finding, update its status in [findings.md](findings.md) and reference the commit hash.

## Tier status

| Tier | Status | Scope |
|---|---|---|
| Tier 0 — Threat model + inventory | ✅ Complete (commits 86cfa9a / beaf58a / ae31719 / 21c9e1a) | editor-surface.md + threat-model.md + controls-map.md + findings.md + this README |
| Tier 1 — Authentication review | ✅ Complete (commits 4e6d893 / c786d90 / 7f42ba0 / be64058 / b335f64 / aadb63c) | tier1-audit.md + decisions.md (D-T1.3) + findings.md updates (F-5 accepted; F-14, F-15 new) |
| Tier 2 — Authorization review | ✅ Complete (commits e25ff12 / 192300c / cfa4e6a / 670212d) | tier2-audit.md + decisions.md (D-T2.4) + findings.md updates (F-4, F-7 accepted; F-9 downgraded) |
| Tier 3 — Input/output handling | ✅ Complete (commits 749aa2c / 280edea / 8730c64 / 1d04462) | tier3-audit.md + decisions.md (D-T3.3 Deferred) + findings.md updates (F-6 accepted); plus polish per Tier 3 review |
| Tier 4 — User-data obligations | ✅ Complete (commit c32195b) | tier4-audit.md + decisions.md (D-T4.1..5) + findings.md updates (F-16 new); 4 Tier 6 implementation items: 3 CLIs + privacy.php refresh |
| Tier 5 — Disclosure + response | ✅ Complete (commit 843207c) | tier5-audit.md + incident-response.md (runbook) + decisions.md (D-T5.1..3); restore-drill plan documented (first execution pending operator). No new findings. |
| Tier 6 — Hardening + closeout | ✅ Complete from dev-side (commits ff107e8 / f769d68 / e4b8fa6 / 5724a23 / this commit) | tier6-audit.md + posture.md. 5 findings Closed (F-1/2/14/15/16); 6 Accepted (F-3/4/5/6/7/8); 2 Deferred to second-maintainer trigger (F-9/13); 3 Open are operator prod-side confirms (F-10/11/12). 3 Tier 4 CLIs shipped (delete-editor / export-editor / editor-retention) + systemd timer. Operator action list in tier6-audit.md § 6.3. |

## Out of scope

Documented in `docs/done/PLAN_editor_security_review.md` under "Out of scope":
- External pentest, WAF, code-signing/SBOM, compliance certifications, DDoS protection beyond Hetzner default, PHP-layer code refactor (tracked separately in `docs/done/PLAN_php_layer_split.md`).
