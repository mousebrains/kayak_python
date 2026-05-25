# Kayak — Deep Project Review (Round 2)

> **Archived 2026-05-25 — historical record, not a description of current state.**
> This was `REVIEW.md` on the now-superseded `project-review-2` branch. Every finding
> below was remediated in **PR #25** (see the Remediation log at the end). It is kept
> as a **trend baseline** — the grade series and dimension rubric — and for the
> **standing structural thesis**, not as a live findings list. For the next round:
> review the code cold *first*, then reconcile against this, so prior findings don't
> anchor the new sweep.

**Reviewed:** 2026-05-24 · branch `project-review-2` (off `main` @ `3a7553f`)
**Prior review:** 2026-05-23 (graded **B−**). Its findings were largely remediated in PR #18 (`b4bf755`). This round verifies those fixes held *and* audits the new surface added by PRs #19–#24.
**Method:** 5 parallel facet audits (docs/onboarding, Python `src`+`scripts`, PHP/frontend, schema/data/bloat, CI/testing/deploy). Every CRIT/HIGH re-verified against source — not taken on an agent's word. Iterated to convergence.
**Scope:** entire tracked repo, all layers. Emphasis on the four axes you called out — self-consistency, drift, new-maintainer docs, bloat — plus a fifth this round forced to the front: **test coverage of recently-merged work.**

---

## Executive summary

The good news is real and verified: **PR #18 fixed the prior review's findings, and they hold.** Re-checked against source — the documented quick-start now produces a working site (`init-db --no-seed` → `import_metadata.py` → `pipeline`); the four atomicity-breaking migrations are clean; the installer enables all **15** timers; `biome` now lints every JS file; `ruff` is pinned in CI; Dependabot is live (and has already landed #19/#20); the schema doc matches `models.py` exactly; the retired nginx monoliths are gone. Schema/migration consistency rose to **A−**, deploy **D+ → C**, CI infra **C+ → B−**.

The bad news is structural, and it is the **same diagnosis as last round.** The feature wave that immediately followed the cleanup — **#22** (`reaches.json` geom snapshot + `--geom-only`), **#23** (`find_huc4` nearest-flowline rework), **#24** (reach-detail consolidation + gradient-chart elevation profile) — **recreated the exact anti-pattern the cleanup had just retired.** All three shipped with **zero tests**, and #22/#24 added fresh docs-and-deploy drift — including a doc that now *actively misdirects disaster recovery*, and a committed 2.3 MB artifact that `deploy.sh` never applies to prod. The code itself is competent; the discipline at the seams (tests, docs, deploy wiring for new work) is still the gap — which confirms it is structural to a one-author project, not a one-time lapse.

### Overall grade: **B−**
The verified fixes earned a bump; the untested-and-undocumented new wave cancelled it.

| Axis | Grade | One-line |
|---|---|---|
| Self-consistency | **B−** | Schema/atomicity fixed; but the new elevation line re-introduces the inline-style-vs-`.gp-*`-class split the *last* review flagged, `_localize` is still triplicated, gradient now has three fighting sources of truth, `check_reaches`/`_check_reaches` dup remains. |
| Drift (docs/config vs reality) | **C+** | Quick-start/timers/deploy-config fixed; but `migrations.md` now contradicts the working rebuild path it should describe (CRIT), `reaches.json`/`--geom-only` is documented nowhere, CHANGELOG omits 6 merged PRs, `tracing.md` still describes the pre-#23 algorithm. |
| New-maintainer docs | **B−** | The front door genuinely works now; but the *new* operational procedure (push geom to prod) is invisible, and the recovery doc actively misleads. |
| Bloat of pull content | **B−** | Five one-offs relocated, biome glob, clean single-purpose commits; but +2.3 MB `reaches.json`, `huc_name.csv` still 97% dead, `0046` still duplicates the CSV gradient blob, no `.gitattributes`. |
| **Test coverage of recent work** (new) | **D** | #22 + #24 are entirely untested — including a script that mutates the prod DB; `scripts/` is in neither CI gate and already carries a live `C901` violation. |

### Per-dimension grades (Δ vs. last round)

| Dimension | Grade | Δ |
|---|---|---|
| Python package core (`src/kayak/`) | **A−** | = |
| Schema & migration consistency | **A−** | ▲ from B− |
| Data & config consistency | **B+** | = |
| Documentation & onboarding | **B−** | ▲ from C+ |
| CI & testing (infra) | **B−** | ▲ from C+ |
| PHP & frontend | **B−** | = |
| Python overall (incl. `scripts/`) | **B−** | = |
| Tracked-artifact bloat | **B−** | ≈ |
| PR discipline | **C+** | ▲ from D |
| Ops / deploy | **C** | ▲ from D+ |
| **Test coverage of #19–#24 work** | **D** | new |

---

## Findings by theme

Severity: **CRIT** / **HIGH** / **MED** / **LOW**. Evidence is `file:line` / migration / commit id. Every CRIT/HIGH was verified against source this round.

### 0. The round-2 headline — recently-merged work shipped untested

- **CRIT — The entire #22 + #24 surface has zero tests, including a script that mutates the production DB.** Verified: `grep -rl "export_metadata|import_metadata|reaches.json|format_reach_|putin_elev" tests/` returns **nothing**.
  - `scripts/import_metadata.py` is documented as runnable against a *live prod DB* and does `INSERT OR REPLACE` + a `--geom-only` `UPDATE reach SET geom`. No round-trip test, no `--geom-only` test, no empty-string→NULL test. A regression here silently corrupts prod metadata.
  - `php/includes/reach_fields.php` (4 pure formatters, trivially testable) — no unit test, and not exercised end-to-end either (`ReachIntegrationTest` seeds reaches with no `elevation`/`length`/`gradient_profile`, so the formatters never run).
  - `svg_plot.php`'s elevation branch (`generate_gradient_profile_svg` with `putin_elev_ft`/`elev_lost_ft`) — `SvgPlotTest` only ever calls the 4-arg form, so `$has_elev` is always false in tests; the cumulative-integral + right-axis code is dark.
  - The lone counter-example, done right: `#23`'s `tests/test_tracing/test_trace.py::test_find_huc4_prefers_nearest_across_divide` (Nestucca → 1710). It's `importorskip("osgeo")`+cache-gated so CI skips it, but it's the model the rest should follow.
- **HIGH — `scripts/` is linted and type-checked by neither CI gate.** `ci.yml:40` is `ruff check src/ tests/`; `ci.yml:200` is `mypy src/`. `scripts/` is in neither. Concrete consequence: `ruff check scripts/import_metadata.py` → **`Found 1 error`** (its `main()` is `C901` cc≈11, over the repo's own limit) — a violation the package would never be allowed to merge, invisible because the gate doesn't point at `scripts/`.

### 1. Self-consistency

- **MED — The new elevation line re-introduces the exact inline-style inconsistency the last review flagged.** `svg_plot.php` draws the elevation polyline and right-axis labels with inline `stroke="#1565C0"`/`fill="#1565C0"`, while every other element in the same chart (bars, grid, axis, cursor, dot, frame) uses `.gp-*` CSS classes (`style.css:394-405`). It can't be themed via the chart's `--c-*` variables. Should be `.gp-elev`/`.gp-elev-axis`. *(This is my own #24 code — it should have followed the surrounding convention.)*
- **MED — `gradient` state now has three fighting sources of truth.** `max_gradient`/`gradient_profile` are written by `0046` (407-row backfill), partially NULLed by `0051/0054/0055`, lifted by `0058`, then re-restored for 11 rows by `0059` — whose own header (`0059:4-13`) says it exists *only* to repair a fresh-apply ordering hazard the chain created. The same values also live in `reach.csv:gradient_profile`. A future gradient recompute must touch all three or they silently diverge; fresh-apply correctness depends on no one reordering these migrations.
- **MED — `_localize` is still triplicated.** Byte-identical in `parsers/usbr.py` and `parsers/wa_gov.py`, both re-implementing `parsers/base.py:150-157`. (Prior finding; out of #18's scope, unaddressed.)
- **MED — `check_reaches` is still the lone CLI deviant, still forcing a duplicate.** `cli/check_reaches.py` is the only module using `addArgs = _addArgs` and whose handler calls `sys.exit()`; `pipeline.py` still carries the parallel `_check_reaches()` workaround (2 refs) because it can't reuse the exiting entry point. (Prior finding, unaddressed.) The clean fix (return an int / raise; let `main.py` map the exit code) was not taken.
- **LOW — Map JS still fully duplicated.** `reach-map.js` remains a near-verbatim subset of `feature-map.js`; `map.js` duplicates the OSMB-overlay logic (the source even says so). biome's *formatter* is still disabled (`biome.json`), so no house style is enforced across the JS. (Prior finding, unaddressed.)
- **LOW — `source.agency` still carries multiple identities for the same data.** `init_db.py:109-115` stamps the lowercase parser name (`nwps`, `wa.gov`, `nwrfc.textplot`) into `agency` for YAML-station sources while curated `source.csv` rows use `NWS`/`USGS`/`NWRFC` (live: USGS×171, NWS×73 … vs nwps×5, wa.gov×6 …); calc sources split a third way (8 `Calculation` vs 7 empty). Any `GROUP BY agency` mis-counts. (Carried over; unaddressed.)

### 2. Drift (documentation/config vs. reality)

- **CRIT — `docs/migrations.md:210-216` now states the opposite of reality, in the worst possible place.** It tells the operator that **"Rebuilding prod from scratch … is not possible purely from what's checked in"** and that the CSVs are **"nightly auto-snapshots, not seeds … no code path reads them back into the DB."** Both are false as of #18/#22: `scripts/import_metadata.py` reads every CSV back (the quick-start and SETUP.md §4 both invoke it), and #22 added `reaches.json` + `--geom-only` as the geom-apply path. An operator hitting corruption and reading this would conclude recovery is impossible and *not run the one command that works.* This is the single most dangerous line in the repo.
- **HIGH — `reaches.json` and `--geom-only` (#22) are documented nowhere but the script's own docstring.** Verified: zero hits across README, CLAUDE, SETUP.md, `docs/`, CHANGELOG. The change is significant and invisible: `geom` was *removed* from `reach.csv` and moved to a tracked 2.3 MB `reaches.json`; the intended prod re-trace workflow is `import_metadata.py --geom-only`; and this *bypasses* the "reach backfills go via SQL migration" convention stated in CLAUDE.md — an undocumented convention split.
- **HIGH — The `reaches.json` geom is committed but `deploy.sh` never applies it.** `deploy.sh` runs `pull → validate-config → migrate → emit-config → build`; it has no `import_metadata`/`--geom-only` step. So after a dev re-trace (#23 reworked HUC4 detection precisely to *fix* geometry), the maintainer commits `reaches.json`, `deploy.sh` pulls it — and prod `reach.geom` silently goes stale, with no runbook to push it. A committed-but-never-applied artifact is a latent data-staleness bug.
- **HIGH — `CHANGELOG.md [Unreleased]` is stale and omits six merged PRs.** `v1.1.0` and `v1.1.1` tags now exist, but `[Unreleased]` still describes the **#18** fixes as unreleased and contains **none** of #19 (Dependabot), #20 (actions), #21 (biome), #22 (reaches.json), #23 (find_huc4), #24 (consolidation/elevation). The curated history of record is missing a quarter of the project's merged work.
- **MED — `docs/tracing.md:78-80` still describes the pre-#23 HUC4 algorithm.** It says HUC4 is chosen by "checking which GPKG's flowline *extent contains* the put-in" — the exact extent-containment logic #23 replaced (it mis-detected 88/407 reaches near divides). The doc describes the known-buggy algorithm as current, and is internally inconsistent (it mentions "nearest flowline" for take-out trimming elsewhere).
- **MED — README quick-start omits the `OUTPUT_DIR` clobber caveat.** `README.md:67` says `php -S … -t public_html` with no warning that `levels build` with unset `OUTPUT_DIR` writes back into the repo's `public_html/` and clobbers the tracked dev symlinks. CLAUDE.md and SETUP.md warn about this; the README (the front door) never mentions `OUTPUT_DIR`. A contributor following only the README clobbers their tree.
- **HIGH — The `docs/security/` suite's code anchors rotted after the 2026-05-14 handler split, and #14's "repoint" pass missed the suite.** The Tier-5 PHP split extracted `auth_magic_link.php` / `propose_handler.php` / `review_handler.php`, moving the exact code the audit cites: `controls-map.md:21,80` still point `consume_magic_link` / `safe_next_url` at `php/includes/auth.php`, but both now live in `auth_magic_link.php`; the whitelist refs in `findings.md` point at `propose.php`/`review.php` (now 28–32-line shims); `editor-surface.md:89` cites a `MAGIC_LINK_TTL` constant that exists in **no file** (verified). ~24 refs across three files misdirect. The *analysis* is still correct — only the anchors rotted — but an audit suite's whole value is that its `file:line` evidence resolves. (Traces to the 2026-05-14 split, not #18–#24.)
- **MED — Retired `deploy/levels` is still cited in 8 `docs/security/` files** — including the F-1 "Closed" note that credits an HSTS fix to a deleted file's server block (HSTS now lives in `conf/security-headers.conf:7`). #14 repointed the *living* docs but left the audit suite pointing at the monolith the project retired.
- **LOW — Residual stale references.** `CLAUDE.md:86` lists `usgs` as a parser-name example (it's the OGC *fetch* path, not a registered parser; the 7 registered names don't include it); `docs/done/PLAN_c901_cleanup.md:11` still names the removed `_scan_dir_for_huc4` helper.

### 3. Documentation for new maintainers

- The **deep docs remain genuinely strong**, and the front door is *fixed*: the quick-start works, the schema reference is accurate (table count 24/25, all reach columns incl. gradient/geom/elevation present and correct), the `docs/done/` + `docs/one-offs/` indices exist and are current. This is a real, verified improvement over last round.
- But the **new operational reality is undocumented**: the `reaches.json` geom snapshot, the `--geom-only` apply, and the geom-out-of-CSV split (all #22) live only in a script docstring; `migrations.md` actively misleads on recovery (CRIT above); and the only narrative of the gradient/elevation UI (#24) is `docs/REVIEW_gradient_profile.md` — a stale point-in-time branch review pinned to a pre-#24 SHA, which the *last* review already flagged as throwaway prose that shouldn't be in `docs/`. It's still there.
- **Bus-factor-1, again:** the exact gaps a second maintainer would hit first — "how do I get my re-traced geometry onto prod?", "can I rebuild from the snapshot?" — are the ones with no doc or a wrong doc.

### 4. Bloat of pull content (PR discipline)

- **Commit hygiene improved.** #22/#23/#24 are clean, single-purpose, well-described commits — a real step up from the gradient PR's 13-migration post-merge flip-flop. PR discipline is the most-improved axis. *But the "incomplete on merge" half persists: each shipped without tests or doc updates.*
- **MED — `data/db/migrations/0046` (1.68 MB) still duplicates the `reach.csv` gradient blob.** The same ~1.6 MB `gradient_profile` JSON is now committed twice: once as permanent, append-only migration history (`0046`) and once as snapshot metadata (`reach.csv` col 37, ~83% of the file). A backfill that large arguably belonged in the snapshot path only.
- **MED — `data/db/huc_name.csv` is still 97% dead weight.** 17,038 rows / 668 KB; only the 403 HUC6+HUC8 rows are ever read (`gauge_picker.php`, `custom_gauges_handler.php`, `build/levels.py`). Filtering to levels 6+8 → ~17 KB. *(Note: this was consciously kept in T11 for "self-contained rebuilds" — a defensible call, but the 650 KB of provably-unread rows is the cost; worth a second look.)*
- **MED — `import_metadata.py`'s non-`--geom-only` path can silently null a reach's geom.** `INSERT OR REPLACE INTO reach (…)` with a column list that excludes `geom` (it's not in `reach.csv`) deletes+reinserts the row, resetting `geom` to NULL; it's only restored for ids present in `reaches.json` (which filters `WHERE geom IS NOT NULL`). A reach with geom in the live DB but absent/empty in the committed snapshot loses its geometry on a full import. *(My #22 code; the docstring's "won't cascade-nuke" safety note doesn't cover this same-table column reset.)*
- **LOW — Two large per-reach blobs (~4 MB) committed, no `.gitattributes`.** `reaches.json` (2.38 MB) + `reach.csv` gradient_profile (1.71 MB) + `huc_name.csv` (668 KB) dominate the 7.8 MB `data/db/` tree, and none are marked `-diff`/`linguist-generated`, so every metadata edit produces multi-thousand-line diffs. The geom-out-of-CSV split (#22) already cut the worst per-edit churn; a one-line `.gitattributes` would finish the job.
- **LOW — `import_metadata.py` reports success against the wrong/empty DB.** `_default_db_path()` swallows all exceptions (`except Exception: pass`) with no log line, and the run prints `len(geoms)` rather than `cur.rowcount` — so `--geom-only` against a mis-resolved or empty DB matches 0 rows and still reports the full count as "loaded." *(My #22 code.)*

---

## What's genuinely good (verified this round)

- **#18's fixes held under re-verification — every one.** Quick-start works; `0052/0054/0055/0056` are clean (BEGIN/COMMIT-set ≡ `@no_transaction`-set ≡ {0012, 0046, 0059}); installer has all 15 timers; `biome check` lints 15 JS files incl. `gradient-profile.js`; `ruff==0.15.11` pinned; `.github/dependabot.yml` sane; schema doc ↔ `models.py` ↔ `live_schema.sql` in lockstep; retired nginx monoliths deleted.
- **The package core stays A−.** The pipeline DAG, the parser registry, `tracing/format.py`, the rDNS resolver — careful, typed, documented.
- **#23's `find_huc4` rework is correct and well-tested-where-it-can-be.** Nearest-flowline resolution + put-in/take-out agreement, with a real regression test. (Two nits: it's a full O(all-gpkgs) scan now with no early-exit, and the disagreement *tiebreak* branch itself has no test.)
- **`reaches.json` is a faithful, deterministic, justified artifact.** Round-trips byte-stable (`export_metadata` → `import_metadata`), internally consistent with `reach.csv` (all 407 ids match; geom genuinely excluded from the CSV), and the "not regenerable on prod" rationale is legitimate (no DEM/NHD on the CPX11). `reach_fields.php` is phpstan-L8 clean with zero baseline entries; the elevation cumulative-integral math is sound and the JS readout reconstructs the drawn line exactly.
- **`deploy.sh` is otherwise disciplined** (refuses dirty tree, ff-only, validate-before-migrate, atomic emit-config, NOTICE-on-config-change) — its only hole is the missing geom-apply step.

---

## Prioritized remediation

**Tier 1 — correctness / dangerous docs (do first):**
1. **Rewrite `docs/migrations.md:205-216`** — the "rebuild is impossible / CSVs aren't read back" claims are now false and dangerous; point to `init-db --no-seed` → `import_metadata.py` (+ `--geom-only` for geom) as the supported rebuild/recovery path.
2. **Add a test for the metadata round-trip** (`tests/test_scripts/…`): `export_metadata` → `import_metadata` → assert-equal, plus a `--geom-only` case and the geom-null edge. This is the prod-mutating script with no net.
3. **Document + wire the `reaches.json`/`--geom-only` prod-apply** — add the step to `SETUP.md`/CLAUDE, and to `deploy.sh` (apply when `data/db/reaches.json` changed between SHAs), so committed geom actually reaches prod.

**Tier 2 — drift / test coverage:**
4. Fill `CHANGELOG [Unreleased]` with #19–#24 (or cut a release); it's currently missing six merged PRs.
5. Update `docs/tracing.md:78-80` to the #23 nearest-flowline algorithm.
6. Add an integration assertion for the consolidated reach-detail lines + a unit test for the four `reach_fields.php` formatters (edge cases: only-low/only-high, mixed data types); pass the elevation params in `SvgPlotTest`.
7. Add `scripts/` to the CI `ruff`/`mypy` gates (and fix the `import_metadata.main` C901).

**Tier 3 — consistency / bloat:**
8. Make the elevation line a `.gp-elev`/`.gp-elev-axis` CSS class (match the chart's convention; the prior review flagged this exact pattern).
9. Factor `M_TO_FT` into `kayak.tracing` — 1 active copy (`scripts/refresh_reach_elevations.py`, no canonical home) plus 4 in archived `docs/one-offs/` (acceptable per the archive rule); minor, finishes the dedup #18 (T9) started.
10. `.gitattributes`: `data/db/reaches.json -diff`, `data/db/reach.csv -diff linguist-generated`.
11. Revisit `huc_name.csv` (trim to 6+8 or regenerate) and the `0046`-duplicates-CSV gradient storage; decide a single source of truth for gradient.
12. Address the carried-over `_localize` ×3 and `check_reaches`/`_check_reaches` duplication.

---

## Convergence note

Round 1: five parallel facet reviewers (docs, Python, PHP/frontend, schema/bloat, CI/deploy). Round 2: every CRIT/HIGH re-verified against source — confirming all of them and sharpening the `migrations.md` item to CRIT (it doesn't just omit the rebuild path, it asserts the path doesn't exist). Round 3: a convergence sweep of the surface Round 1 under-covered — the editor/auth security layer, the static-build layer, and a data/config re-check — which **confirmed clean** on the build layer, secrets hygiene, `.gitignore`, systemd/`conf` consistency, cross-CSV FK integrity, parser↔YAML matching, and (notably) the #24 rendering surface (no auth/injection added). It surfaced one new cluster — the `docs/security/` reference rot above (documentation drift, no new correctness issue) — plus the carried-over `agency` identity split. With the major facets (R1) and the under-covered surface (R3) both swept and no new *correctness* findings emerging, the review converged here.

**Thesis:** the prior review's findings were genuinely fixed and the fixes hold — but the very next feature wave (#22–#24) recreated the same category of debt: clean code, shipped fast, with no tests and unreconciled docs/deploy drift. The craftsmanship is high and improving; the seam-discipline gap is structural to a single-author project and will keep recurring until "tests + docs + deploy wiring" is part of *merge*, not a follow-up review.

---

## Remediation log — 2026-05-24 (branch `review-fixes-2`, 18 commits)

Implemented Tiers 1–3 to convergence, all gates green, then a two-agent convergence re-review over the full diff surfaced two doc-accuracy issues *introduced by this branch* (a redundant `--geom-only` runbook step + an undocumented upsert abort mode) — both fixed in `9337d3a`. A re-check found no further findings. A pre-PR review then caught three doc-anchor inaccuracies — two in the anchor-repoint commit itself (the `/propose.php` GET-lookup → `_load_propose_context`; the F-1 HSTS-location prose) plus a README step number — fixed (with an optional `int`-return bool guard) in `0c2e40e`; the remediation was then recorded in `CHANGELOG.md [Unreleased]` (`837a794`). A third deep review (three parallel agents over the full branch diff) found no must-fix code defects but a batch of polish items — two more security-doc anchors the repoint had missed (`review.php:281` → `_render_review_list`; the 2.2.5 GET read → `_render_review_detail`), a silent pre-commit↔CI mypy scope gap, a `.gitattributes` overstatement, and `_apply_geom` traceback-hardening — fixed in `a9d9741`. The `docs/one-offs` archive was then linted rather than excluded (RUF059 + formatting fixed, C901 per-file-ignored, added to the ruff gate) in `b6f50ae`, and a convergence-confirmation pass surfaced a single LOW CHANGELOG-wording nit, fixed in `11e096d`. **Branch converged:** all 10 pre-commit hooks, 947 tests, and mypy (now at pre-commit↔CI parity) green; every load-bearing claim re-verified against source.

**Tier 1 — correctness / dangerous docs**
- CRIT `migrations.md` "rebuild impossible / CSVs never read back" → **Resolved** `f8b475e` (runbook rewrite) + `9337d3a` (accuracy fix: 3-command runbook now genuinely matches the quick-start).
- CRIT #22 + #24 shipped untested → **Resolved** `cff05d5` (metadata round-trip test incl. the geom-preservation regression — verified to fail against the old `INSERT OR REPLACE`) + `a3b06c5` (`reach_fields` unit + `SvgPlot` elevation branch + reach/description end-to-end consolidated-line assertions).
- MED `import_metadata` silent geom-null + reporting → **Resolved** `cff05d5` (PK upsert preserves columns absent from the CSV; reports `cur.rowcount`; logs the `_default_db_path` fallback; decomposed `main()` → C901 cleared). Upsert now *aborts* on a non-PK UNIQUE collision instead of silently delete+reinserting — documented (`9337d3a`); `--no-seed` required for a pre-seeded DB.
- HIGH `reaches.json` committed but `deploy.sh` never applied it → **Resolved** `f8b475e` (`--geom-only` step, gated on the file changing between SHAs).
- HIGH `reaches.json`/`--geom-only` undocumented → **Resolved** `f8b475e` (`deploy/SETUP.md` §4 workflow + `CLAUDE.md` migration-exception note).

**Tier 2 — drift / coverage**
- HIGH CHANGELOG omits #19–#24 → **Resolved** `e7ef9b6`.
- MED `tracing.md` stale HUC4 algorithm → **Resolved** `e7ef9b6` (now the #23 nearest-flowline + endpoint agreement).
- MED README `OUTPUT_DIR` caveat → **Resolved** `e7ef9b6`.
- HIGH `scripts/` in neither CI gate → **Resolved** `74c7445` (ruff over all of `scripts/`; mypy over `import_metadata`/`export_metadata` via a new `py.typed`). `refresh_reach_elevations.py` left out of the mypy gate — it imports `httpx`, which is undeclared/unlocked (see "New issue" below).
- HIGH `docs/security/` anchors rotted after the 2026-05-14 split → **Resolved** `e0f70bf` (27 anchors → durable file+function; F-1 HSTS → `conf/security-headers.conf:7`) + `0c2e40e` (pre-PR fix: the `/propose.php` GET-lookup anchor → `_load_propose_context`; the F-1 HSTS-location prose).
- MED `deploy/levels` in security docs → **Resolved (HSTS/F-1)** `e0f70bf`; the non-HSTS log-path citations are kept as point-in-time audit evidence per the suite's own `README.md` note (conscious, not drift).

**Tier 3 — consistency / bloat**
- MED elevation inline-style vs `.gp-*` classes → **Resolved** `54a2598` (`.gp-elev`/`.gp-elev-axis` + `--c-elev`, light `#1565C0` / dark `#a9d0f5` — dark value worth a visual eyeball).
- MED `_localize` triplicated → **Resolved** `6696d1c` (hoisted to `BaseParser`).
- MED `check_reaches` lone CLI deviant → **Resolved** `6d298f0` (returns an int; `main.py` maps it; `addArgs` alias dropped). The pipeline's `_check_reaches` adapter intentionally stays — it's the consistent raise-on-failure pattern (identical to `_orphan_check`) the exception-based orchestrator needs, logging at error level for the OnFailure chain; both reuse `scan_for_issues`.
- LOW `M_TO_FT` no canonical home → **Resolved** `79e6741` (`kayak.tracing.constants`).
- LOW no `.gitattributes` → **Resolved** `fe594af`.
- stale `docs/REVIEW_gradient_profile.md` + `PLAN_c901` helper name → **Resolved** `fe594af`.
- LOW `CLAUDE.md` `usgs`-as-parser example → **Moot**: verified CLAUDE.md only references `fetch-usgs-ogc` (correct); no stale `usgs` parser example exists.

**Deferred (rationale)**
- Gradient single-source-of-truth (`reach.csv` `gradient_profile` ↔ migration `0046`): `0046` is immutable append-only history; the only actionable change (move `gradient_profile` out of `reach.csv`, mirroring geom→`reaches.json`) is feature-sized — its own PR.
- `huc_name.csv` 97% unread: reverses the conscious T11 self-contained-rebuild decision (the review itself calls keeping it defensible).
- Map-JS dedup + biome formatter enable; `source.agency` identity normalization: carried-over LOW, low ROI / data-semantics care.

**New issue discovered (not in the findings above), deferred:** `scripts/refresh_reach_elevations.py` imports `httpx`, which is **not declared** in `pyproject.toml` and **not in `uv.lock`** — the script can't run in a clean env, and that's why it stays out of the mypy gate. Fix: declare `httpx` (dev dep) or port to the already-present `aiohttp`. Its own small change.

**Gates at convergence:** `ruff check`/`format` (`src tests scripts`) · `mypy src` + core scripts · `pytest` 946 pass / 1 skip (osgeo) · `phpunit` 172 · `phpstan` L8 · `php-cs-fixer` · `biome` — all green. Held for prod cross-check before push/PR.
