# Completed plans (`docs/done/`)

These are **finished** planning and review docs, kept for rationale and provenance — not
active TODO lists. Several are still cited by live code and docs (systemd unit
comments, `operations.md`, `slo.md`, sibling plans), so they are reference
material rather than a deletable graveyard. For work *in flight*, see the
`PLAN_*.md` files in `docs/` (root).

| Plan | What it covered |
|---|---|
| `PLAN_dataset_separation.md` | The full code/data separation — engine ↔ `kayak_data` split, dataset contract, paired-release cutover (S1–S9, batches 1–6); COMPLETE 2026-06-19 |
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
| `PLAN_4c_renderers.md` | Batch 4C: host-config renderers + the paired-release cutover; live 2026-06-15 |
| `PLAN_b5_init_dataset.md` | Batch 5 / S5: `levels init-dataset` + new-region runbook; merged #206 |
| `PLAN_kalama_licor_gauge.md` | Replace the Kalama Italian-Cr calc gauge with the real LI-COR sensor; engine #210 + kayak_data #67/#69 merged + deployed to prod 2026-06-21 (release 24ee70c) |
| `PLAN_wa_kalama_coweeman_toutle_tilton.md` | SW-Washington inventory: Kalama/Coweeman/Toutle/Tilton gauges + reaches + calc strategy (calcs wired; Kalama since moved to LI-COR) |
| `REVIEW_round2_2026-05-24.md` | Round-2 deep project review (graded B−); findings landed in #25, kept for trend + standing thesis |
| `REVIEW_round3_2026-05-25.md` | Round-3 deep project review (graded B−); remediated across #34–#52, kept for trend |
| `PLAN_round3_remediation.md` | Round-3 remediation plan (7 phases) — as-built: landed #34–#52; all deferrals but OSMB-dedup (R7.1) since shipped (#47–#52) |
| `REVIEW_round4_2026-05-26.md` | Round-4 deep project review (graded B, ▲ from B−) — audits the round-3 fix surface; remediated across #53–#69, kept for trend |
| `PLAN_round4_remediation.md` | Round-4 remediation plan — as-built: landed #53–#69; R1.1/R1.2/R1.3 recorded "out-of-band on prod" but never landed in the repo (caught + corrected by round-5; see its erratum, fixed #85/#86) |
| `REVIEW_round5_2026-05-29.md` | Round-5 deep project review (graded B−, ▼ from B) — verification-integrity downgrade: round-4's R1.1/R1.2/R1.3 recorded done but never landed; remediated #85–#91, kept for trend |
| `PLAN_round5_remediation.md` | Round-5 remediation plan — as-built: landed #85–#91, each fix shipping a committed guard; the R2.1 claim-vs-source lever (`test_remediation_claims.py`) now enforces archived Verifies |
| `IMPL_round5.md` | Round-5 PR-by-PR implementation playbook — the as-executed sequence for the round-5 plan |
| `REVIEW_round6_2026-05-30.md` | Round-6 deep project review (graded B+, ▲ from B−) — first clean recursive pass; merged #99; surfaced the metadata migration↔CSV duality root cause |
| `PLAN_round6_remediation.md` | Round-6 remediation plan (the v1→v5 red-team record) — **superseded** by the metadata-single-source redesign (`PLAN_metadata_single_source.md`); kept for provenance |
| `PLAN_metadata_single_source.md` | The metadata-single-source-of-truth redesign (design v2) — COMPLETE: stable ids + base-62 `?h=` handles, `sync-metadata`, retire data migrations, data-repo split (`kayak_data`) + branch protection (#100–#107) |
| `REVIEW_gpt-5.5_2026-06-03.md` | External gpt-5.5 project review, round 1 — healthcheck per-source freshness false negative (HIGH), stale emit-config runbook, broken PHP quick start, reply race, validate-config gaps; remediated in #119, kept for trend |
| `REVIEW_gpt-5.5_take2_2026-06-03.md` | gpt-5.5 follow-up review of the round-1 fixes — the Turnstile secrets-merge hole in the R1.5 wrapper pipeline (confirmed fired in prod: captcha silently off); remediated in #119 |
| `preflight-2026-05-10.md` | Pre-production preflight / closing record for the 2026-05-20 cutover to `levels.wkcc.org` (all P0/P1 items resolved) |
| `PLAN_montana_gauges.md` | Montana USGS gauges (curated 13-site list) — shipped in release 1.1.0 (#10, #13); the third-revision plan's leftovers were overtaken by events (state pages kept; `0036` regeneration mooted by the migration retirement) |

> `docs/PLAN_production_discipline.md` lives in `docs/` (not here) on purpose:
> it is a landed plan but remains a live cross-reference target for the systemd
> units, `operations.md`, and `slo.md`, so it stays alongside the active docs.
