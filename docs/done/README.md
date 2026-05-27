# Completed plans (`docs/done/`)

These are **finished** planning and review docs, kept for rationale and provenance — not
active TODO lists. Several are still cited by live code and docs (systemd unit
comments, `operations.md`, `slo.md`, sibling plans), so they are reference
material rather than a deletable graveyard. For work *in flight*, see the
`PLAN_*.md` files in `docs/` (root).

| Plan | What it covered |
|---|---|
| `DNS.CHANGEOVER.md` | DNS cutover bringing `levels.wkcc.org` onto the host |
| `PLAN_assoc_reaches_card.md` | Card-ify the Associated Reaches list on phone portrait |
| `PLAN_c901_cleanup.md` | C901 complexity-lint cleanup for the grandfathered scripts |
| `PLAN_dev_env_followups.md` | Dev-environment follow-ups |
| `PLAN_editor_security_review.md` | Security review of the editor / Comment feature |
| `PLAN_internal_dashboard.md` | The `/_internal/` operator dashboard (Phase 2.4) |
| `PLAN_js_cleanup.md` | JS cleanup — close the lint gap + `var` decision |
| `PLAN_js_cleanup_phase3.md` | Phase 3: `var → const/let` modernization |
| `PLAN_js_smoke_tests.md` | JS load/smoke tests via Playwright in CI |
| `PLAN_logs_analyze_migration.md` | Migrate `~/logs.analyze` into `levels analyze-logs` |
| `PLAN_map_and_ui_tweaks.md` | Map & UI tweaks |
| `PLAN_orphan_sources.md` | Stop fetch silently feeding orphan sources (orphan-check) |
| `PLAN_outstanding_followups.md` | Outstanding-follow-ups closeout schedule |
| `PLAN_pacificorp_rogue.md` | PacifiCorp Rogue Bypass parser + Rogue-above-Prospect calc gauge |
| `PLAN_php_layer_split.md` | PHP entry-point → handler split discipline |
| `PLAN_php_testing.md` | PHP test harness + coverage ramp (Functional/Integration cases, #31) |
| `PLAN_phpstan_level9_strict.md` | PHPStan level 9 + full strict-rules, shrinking baseline (#29) |
| `PLAN_pre_release_followup.md` | Pre-release follow-up after the 2026-05-13 audit |
| `PLAN_three_instance_layout.md` | Three-instance host layout (prod / test / tpw) |
| `PLAN_tier3_closeout.md` | Typed-config spine (T3.3) + `KAYAK_HOME` (T3.4) + dormant-schema decision (T3.5) |
| `PLAN_gradient_single_source.md` | Single-source `reach.gradient_profile` — snapshot out of `reach.csv` (R6.1, #42) |
| `REVIEW_round2_2026-05-24.md` | Round-2 deep project review (graded B−); findings landed in #25, kept for trend + standing thesis |
| `REVIEW_round3_2026-05-25.md` | Round-3 deep project review (graded B−); remediated across #34–#52, kept for trend |
| `PLAN_round3_remediation.md` | Round-3 remediation plan (7 phases) — as-built: landed #34–#52; all deferrals but OSMB-dedup (R7.1) since shipped (#47–#52) |
| `REVIEW_round4_2026-05-26.md` | Round-4 deep project review (graded B, ▲ from B−) — audits the round-3 fix surface; remediated across #53–#69, kept for trend |
| `PLAN_round4_remediation.md` | Round-4 remediation plan — as-built: landed #53–#69 (each anti-drift guard front-loaded with its fix); R1.1/R1.3 prod-host items out-of-band |

> `docs/PLAN_production_discipline.md` lives in `docs/` (not here) on purpose:
> it is a landed plan but remains a live cross-reference target for the systemd
> units, `operations.md`, and `slo.md`, so it stays alongside the active docs.
