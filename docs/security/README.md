# `docs/security/`

Security artifacts for the editor pipeline. Maintained per `docs/PLAN_editor_security_review.md`.

## Contents

| File | Purpose | Updated when |
|---|---|---|
| [editor-surface.md](editor-surface.md) | Inventory: 10 PHP entry points, 7 DB tables (with PII/credential tags), cookies, rate limits, external services, logging surface. The "what exists" source of truth. | A new editor endpoint, table, cookie, rate limit, or external service joins the pipeline. |
| [threat-model.md](threat-model.md) | STRIDE-style threat enumeration (29 threats, 3 actor profiles). The "what could go wrong" reference. | A new threat surfaces (e.g. a new attack technique applicable to existing assets), or a new asset class joins the surface. |
| [controls-map.md](controls-map.md) | Threat → control mapping with `file:line` refs and ✓/⚠/✗/⊘ status. The "what defends us" reference. | A control changes (added / removed / weakened) or a threat is revised. |
| [findings.md](findings.md) | Open gaps + decisions, with per-finding remediation options + plan-tier allocation. The action tracker. | A finding is filed / dispositioned / closed. |

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
| Tier 0 — Threat model + inventory | ✅ Complete (commits 86cfa9a / beaf58a / ae31719 / this commit) | This README + the 4 docs above |
| Tier 1 — Authentication review | ⏳ Pending | 5 phases + decision point on maintainer 2FA |
| Tier 2 — Authorization review | ⏳ Pending | 4 phases + decision point on audit-trail tamper resistance |
| Tier 3 — Input/output handling | ⏳ Pending | 5 phases + decision point on file-upload retention |
| Tier 4 — User-data obligations | ⏳ Pending | 5 decision points (deletion / export / retention / privacy / security.txt) |
| Tier 5 — Disclosure + response | ⏳ Pending | 3 decision points + 2 phases (runbook, restore drill) |
| Tier 6 — Hardening + closeout | ⏳ Pending | Apply findings + decisions; final posture doc |

## Out of scope

Documented in `docs/PLAN_editor_security_review.md` under "Out of scope":
- External pentest, WAF, code-signing/SBOM, compliance certifications, DDoS protection beyond Hetzner default, PHP-layer code refactor (tracked separately in `PLAN_php_layer_split.md`).
