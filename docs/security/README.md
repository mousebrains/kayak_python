# `docs/security/`

Security artifacts for the editor pipeline. Maintained per `docs/PLAN_editor_security_review.md`.

## Contents

| File | Purpose | Updated when |
|---|---|---|
| [editor-surface.md](editor-surface.md) | Inventory: 10 PHP entry points, 7 DB tables (with PII/credential tags), cookies, rate limits, external services, logging surface. The "what exists" source of truth. | A new editor endpoint, table, cookie, rate limit, or external service joins the pipeline. |
| [threat-model.md](threat-model.md) | STRIDE-style threat enumeration (29 threats, 3 actor profiles). The "what could go wrong" reference. | A new threat surfaces (e.g. a new attack technique applicable to existing assets), or a new asset class joins the surface. |
| [controls-map.md](controls-map.md) | Threat → control mapping with `file:line` refs and ✓/⚠/✗/⊘ status. The "what defends us" reference. | A control changes (added / removed / weakened) or a threat is revised. |
| [findings.md](findings.md) | Open gaps + decisions, with per-finding remediation options + plan-tier allocation. The action tracker. | A finding is filed / dispositioned / closed. |
| [decisions.md](decisions.md) | Per-tier decision-point log. Each entry: chosen option, rationale, re-evaluation triggers. | A decision is made at the end of a tier. |
| [tier1-audit.md](tier1-audit.md) | Tier 1 (Authentication) audit log — per-phase test results, pass/fail verdicts, mitigation effort. | Per-phase during Tier 1; final closeout summary at end. |

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
| Tier 2 — Authorization review | ✅ Complete (commits 919baf9 / 2f98898 / 5911288 / this commit) | tier2-audit.md + decisions.md (D-T2.4) + findings.md updates (F-4, F-7 accepted; F-9 downgraded) |
| Tier 3 — Input/output handling | ⏳ Pending | 5 phases + decision point on file-upload retention |
| Tier 4 — User-data obligations | ⏳ Pending | 5 decision points (deletion / export / retention / privacy / security.txt) |
| Tier 5 — Disclosure + response | ⏳ Pending | 3 decision points + 2 phases (runbook, restore drill) |
| Tier 6 — Hardening + closeout | ⏳ Pending | Apply findings + decisions; final posture doc |

## Out of scope

Documented in `docs/PLAN_editor_security_review.md` under "Out of scope":
- External pentest, WAF, code-signing/SBOM, compliance certifications, DDoS protection beyond Hetzner default, PHP-layer code refactor (tracked separately in `PLAN_php_layer_split.md`).
