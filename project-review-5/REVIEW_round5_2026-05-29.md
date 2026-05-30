# Kayak — Deep Project Review (Round 5)

**Reviewed:** 2026-05-29 · branch `main` (live tree @ `f3ed673`)
**Prior rounds:** R1 (05-23) **B−** → R2 (05-24) **B−** → R3 (05-25) **B−** → R4 (05-26) **B**,
archived at `docs/done/REVIEW_round4_2026-05-26.md` (remediation `docs/done/PLAN_round4_remediation.md`).
**Method:** 6 parallel cold facet auditors (Python core, PHP/security, schema/migrations/data,
tests/CI, ops/deploy/hardening, docs/hygiene), each told to review *cold*, verify every claim against
source, and judge **two bands**: (A) did round-4's own fixes durably stick, and (B) what did the new
post-round-4 work introduce. **Every HIGH and every security finding was then hand-re-verified against
committed source** (grep/pickaxe/file:line), and the two leads the synthesizer seeded were
deliberately falsifiable — both dissolved (see convergence note). Live DB (fresh pull) +
freshly-regenerated CSVs were used for the data facet.
**Scope:** entire tracked repo, with extra scrutiny on the round-4 remediation surface (#53–#69 — *this
round audits the prior round's own fixes*) and the new feature/data batch (#70–#83 + the metadata
snapshot refresh 8ce7366 + the PENDING_RECONCILIATION emptying f3ed673).

---

## Executive summary

**The new work is the best-executed batch in this project's review history — and round-4's
highest-severity remediation never happened.** Both halves are true, and the second governs the grade.

The bright half is genuinely bright. The Batch A/B/C data work (migrations 0066/0067/0068, the
source-based USGS-OGC refactor 0065/#75) is **clean to a degree no prior round saw**: every one of
the eight new migrations is idempotent and FK-clean, every wired source has a `gauge_source` link and
a live fetch mechanism, `orphan-check` is green, and the freshly-regenerated CSVs reconcile to the
live DB **byte-for-byte on both row counts and content** (zero rows DB-only, zero CSV-only across
source/fetch_url/gauge_source). `PENDING_RECONCILIATION` is *genuinely* empty (f3ed673 transitioned it
from a populated set, not vacuously). The Python core has **no findings** — every R5.x correctness fix
held, the USGS-OGC refactor is correct and load-bearing-tested, models↔migration lockstep is exact.
And the round-4 **mechanized** guards all held *and were re-confirmed to actually guard* (each has a
non-vacuity self-test that fires): schema-doc sync, the factual-CHANGELOG check, the docs/done
orphan-plan check, the migrate↔CSV reconciliation, the PHPStan-level grep, the systemd-unit CI
validation. 983 Python tests pass (80% coverage), 519 PHP tests pass, PHPStan level 9 + strict clean.

The dark half undoes round-4's headline. Round 4 raised the grade B− → B on the explicit claim that
"the three security/ops HIGHs that pinned R3 are closed and re-checked cold … verified durable." For
**three of the four Phase-1 sharp edges, that is false against the committed repo:**

- **R1.1 (HIGH)** — `scripts/db_push.sh:134` still runs `DELETE FROM pages` (`grep -c` → **1**; the
  plan's own Verify line demanded `0`). The latent prod-restore breaker round 4 "fixed" is still armed.
- **R1.2 (HIGH)** — `db_push.sh` has **no** `trap … EXIT` timer-restart guard (`grep -c 'trap.*EXIT'`
  → 0); R1.2 isn't even in round-4's shipped-PR list — it was silently dropped.
- **R1.3 (security MED)** — `php/includes/source_url.php:32` is still `/[\r\n\0]/` (no tab); a
  `git log -S` pickaxe across `--all` proves `/[\r\n\t\0]/` was **never committed on any branch**, and
  no tab regression test was ever added — yet `REVIEW_round4:105` states "the tab fix is **complete**
  as `/[\r\n\t\0]/`."

The one Phase-1 item that shipped *with a committed mechanized guard* — **R1.4**, the review-apply
column allowlist — landed and is airtight, with a real security test (`ReviewLogicFunctionalTest`).
So Phase 1 splits perfectly along the round-4 thesis: **the item routed through a committed,
tested PR held; the two items routed "out-of-band on prod" (⏳) and the one dropped from the PR list
all evaporated.** "Applied out-of-band on the prod host" was the tell — and in *this* repo's deploy
model it is structurally unsound for a tracked file (see §0).

### Overall grade: **B− (▼ from B)** — a verification-integrity downgrade, not a code-quality one

This is the rare round where most dimensions improved and the grade still fell. The drop is entirely
about **trust in the remediation record**: round 4 archived two HIGHs and a security fix as
done/verified-on-prod while none reached the repo. A believed-closed HIGH is the worst drift class
there is — strictly worse than an open finding, because no one is looking. That single failure
outweighs an otherwise excellent batch, because the whole value of the R2→R3→R4→R5 trend is a
*trustworthy* record, and round 5 found the record falsified on its highest-severity items. The code
trajectory is the best it has ever been; the path back to B/B+ is the shortest it has ever been (commit
three small fixes + mechanize a claim-vs-source guard). But the grade reports the state, and the state
includes a hole the prior grade papered over.

| Axis | Grade | Δ | One-line |
|---|---|---|---|
| Self-consistency | **C+** | ▼ | The remediation record contradicts the source: `PLAN_round4:4` "Every finding shipped" but R1.1/R1.2/R1.3 didn't; `REVIEW_round4:105` quotes a regex never committed. |
| Drift (docs/config vs reality) | **B** | ▲ | All six round-4 doc fixes stuck and are mechanized; two new prose drifts (USGS-OGC selection wording, empty CHANGELOG) on un-mechanized surfaces. |
| New-maintainer docs | **B** | ▲ | Schema ref + guards intact; index correct; pulled by the `fetch-usgs-ogc` "for gauges with usgs_id" wording the code's own docstring now contradicts. |
| Ops / deploy | **C+** | ▼ | Two HIGHs still armed in source + falsely recorded done; but R5.6/R5.3/R4.3/R4.2 ops mechanization (backups-out-of-repo, root-unit hardening, mime drift, systemd CI) all held + is A-tier. |
| Security | **B** | ▼ from B+ | R1.4 airtight + tested; auth/CSRF/CSP/SQL core re-verified sound; pulled by R1.3 never landing despite the "complete" claim. |
| Data consistency | **A−** | ▲ from B | Byte-clean DB↔CSV reconciliation on a fresh pull; orphan-clean; PENDING_RECONCILIATION genuinely empty; one reach-118 audit-trail MED. |
| Test coverage of recent work | **A−** | ▲ | Guards verified to actually guard; new data + refactor tested; 80% cov; gaps are LOW completeness (untested map popup, no CI geom-validation). |

### Per-dimension grades (Δ vs. R4)

| Dimension | Grade | Δ |
|---|---|---|
| Python package core (`src/kayak/`) | **A−** | = (no findings; every R5.x fix held; refactor clean) |
| Schema & migration consistency | **A−** | ▲ from B+ (0061–0068 idempotent + FK-clean; lockstep exact) |
| Data & config consistency | **A−** | ▲ from B (byte-clean reconciliation; orphan-clean) |
| Documentation & onboarding | **B** | ▲ from B− (round-4 fixes stuck + mechanized; two new prose drifts) |
| CI & testing infra | **A−** | ▲ from B (guards proven non-vacuous; new work tested) |
| PHP & frontend | **B+** | ▲ from B (R1.4 airtight; map/svg clean) — held back by R1.3 |
| Ops / deploy | **C+** | ▼ from B (two HIGHs never landed; R5.x ops fixes A-tier) |
| Security | **B** | ▼ from B+ (R1.3 never landed despite "complete" claim) |
| PR discipline / record integrity | **C** | ▼ from C+ ("Every finding shipped" is false; R1.2 dropped) |
| Tracked-artifact bloat | **A−** | ▲ from B (zero bloat; `.gitattributes` collapse the JSON snapshots) |

---

## Findings by theme

Severity: **CRIT** / **HIGH** / **MED** / **LOW**. Evidence is `file:line` / migration / commit /
query. **Every HIGH and every security item was hand-re-verified against committed source.** No CRIT
this round. The two HIGHs are latent/low-likelihood (dev-only push script), so the round leads with a
*process* failure, not a live outage.

**Convergence note (over-claims and seeded falsifiables):** the synthesizer handed two facets
deliberately falsifiable leads to test their independence. Both were correctly dissolved: (1) a sanity
query `source.name LIKE '%Crooked%'` returned **zero** rows despite #78 wiring "4 Crooked-basin gauges"
— the data facet proved this a **naming artifact** (the sources are NWS/NWRFC station IDs CRPO3/PRVO3/
CRSO3/OCHO3; the NF Crooked reach is `aw_11293`/id 420, fully present in DB + CSVs + geom, passes
`check-reaches`). (2) The live DB reports **27 tables** vs CLAUDE.md's "25" — the docs facet proved the
doc **correct**: 24 ORM + `schema_migrations` = 25 named, plus `sqlite_stat1`/`sqlite_stat4` (SQLite
ANALYZE internals, correctly uncounted). No auditor inflated to confirm the seed. R1.1/R1.2 were
surfaced independently by the ops facet and R1.3 by the PHP facet — corroboration, not coordination.

### 0. The headline — round-4's Phase-1 sharp edges never landed

> **Verdict on the round-4 remediation:** the mechanized + tested half (R1.4, all of Phase 2–5) shipped
> and held. The "out-of-band on prod" half (R1.1, R1.3) and the silently-dropped item (R1.2) did not
> reach the repo at all. Round 4's grade-raising rationale — "the security/ops HIGHs are closed and
> verified durable" — is counterfactual for three of the four Phase-1 items.

- **HIGH [ops, latent — round-4 regression] — `scripts/db_push.sh:134` still runs `DELETE FROM pages`.**
  Evidence: `grep -c 'DELETE FROM pages' scripts/db_push.sh` → **1** (line 134); `PLAN_round4_remediation.md:40`
  specified "delete line 134" with **Verify: `grep -c` → `0`**. `git diff 22adc7c..HEAD -- scripts/db_push.sh`
  shows the *only* post-baseline change is the R5.6 backup-path move. The live DB confirms the trigger is
  armed: `pages` absent from `sqlite_master`, `0006_drop_pages.sql` applied. The merge SQL block runs
  standalone under `set -euo pipefail`, so `no such table: pages` aborts the remote heredoc **before** the
  integrity check (`:140`), the archive/swap (`:153–159`), and the timer-restart (`:176–179`) — stranding
  the 4 pipeline/backup timers stopped (`:100–106`) and the new DB uninstalled. Reproduced (sqlite3 exits
  1 on the missing table). Low real-world likelihood (dev-only script, operators told never to run it),
  high impact. **Fix:** delete line 134 **in a committed PR** — not out-of-band on prod (see structural note below).

- **HIGH [ops, latent — round-4 regression] — `db_push.sh` has no `trap … EXIT` timer-restart guard.**
  Evidence: `grep -c 'trap.*EXIT' scripts/db_push.sh` → **0**. `PLAN_round4_remediation.md:41` (R1.2)
  specified a `trap 'sudo -n systemctl start <4 timers>' EXIT` covering the stop→restart critical section,
  cleared (`trap - EXIT`) only after a successful restart. The committed script restarts timers in exactly
  **one** place — the integrity-check failure branch (`:145–149`). Five failure points after the timer-stop
  (`mv` :154, `gzip` :155, `mv` :159, `chmod` :160, the prune :167) have **no** restart coverage: any
  non-zero there exits with all 4 timers stopped. R1.2 was an `(S, low)` plan item with **no PR number in
  the header's shipped list** — silently dropped. **Fix:** install the `trap … EXIT` guard, in a committed PR.

- **MED [security, CSP-mitigated — round-4 regression] — `sanitize_source_url()`'s control-char filter
  still omits TAB; the R1.3 fix never landed and was falsely recorded "complete."** Evidence:
  `php/includes/source_url.php:32` is `preg_match('/[\r\n\0]/', $raw)` — no `\t`. `git log --all -S '\r\n\t\0'
  -- php/includes/source_url.php` returns **nothing** (never committed on any branch). Empirically,
  `j⇥avascript:` / `da⇥ta:text/html,…` pass through unchanged (the tab makes `parse_url` see no scheme, so
  the `:38` scheme check is skipped and the value falls through the relative-path branch `:41`), then render
  as a clickable `href` in the maintainer review UI (`review_handler.php:269`); `htmlspecialchars` preserves
  the literal tab and browsers strip TAB/LF/CR per the WHATWG URL spec → live `javascript:`/`data:` on click.
  Reachable by any signed-in editor incl. the lowest `pending` tier. `SourceUrlTest.php:85–91` has `\r\n\0`
  cases but **no tab case**. CSP `script-src 'self'` blocks `javascript:` execution and browsers block
  top-level `data:` nav, so this is defense-in-depth — but the sanitizer is the *intended primary* defense
  and it regressed, and `REVIEW_round4:105` asserts it "complete as `/[\r\n\t\0]/`," a regex that was never
  committed. **Fix:** change the filter to `/[\r\n\t\0]/`; add the `j\tavascript:` / `da\tta:` regression cases.

- **MED [self-consistency, trust] — the round-4 remediation record asserts work that the source
  contradicts.** Evidence: `PLAN_round4_remediation.md:4` "Every finding shipped across PRs #53–#69";
  `:3` "Every finding shipped"; `:7` "the R1.1/R1.3 prod-host items were applied out-of-band (⏳)";
  `:29` "R1.1 and R1.3 are already in flight on the prod system (⏳)". None of R1.1/R1.2/R1.3 is in the
  committed repo. This is the cardinal failure for a review process: the artifact whose job is to track
  whether fixes stick recorded its highest-severity fixes as stuck when they were never applied.
  **Structural note (why "out-of-band on prod" cannot work here):** `/home/pat/kayak` deploys via
  `git pull --ff-only` on `main`, and `deploy.sh:53` aborts on a dirty tree. A hand-edit to a *tracked*
  file is therefore either (a) reverted by the next pull — silently re-arming the bug — or (b) left in
  place, making the tree dirty and blocking all future deploys. For a tracked file, the only sound fix is
  a committed PR; "applied out-of-band on prod" is not a state this repo can hold. **Fix:** land R1.1/R1.2/
  R1.3 as committed PRs, and **mechanize a claim-vs-source guard** (see §5 — the round-5 lever).

### 1. New data & refactor work — verified clean (the bright spot)

> Audited migration-by-migration against the live DB + the freshly-regenerated CSVs. This is the
> cleanest data batch in five rounds; recorded here as *verified*, not as findings.

- **0065 (split conflated USGS sources, #75) — correct.** Both target gauges now aggregate `[USGS]+[NWS]`
  with no dangling refs, no leftover conflated source, no source left with `usgs_id` but no `gauge_source`.
  The 8 gauges carrying `usgs_id` without a USGS source are the documented USGS-dark cases, each with a
  live alternative (verified fresh observations 2026-05-29). The source-based selection (`_build_site_map`
  keys on `Source.agency=='USGS'` via `gauge_source`, `.distinct()`) is provably lossless: 183 USGS sources,
  183 distinct station-id names, zero dupes, none linked to >1 gauge.
- **0066/0067/0068 (Batch A/B/C) — correct.** 4 Columbia temp gauges (Bonneville correctly merges 2 USGS
  sources, `usgs_id` NULL); 12 Lewis reaches (ids 408–419, each with state+class+geom+gradient, 11/12 with
  a guidebook — the `aw_5711` gap is documented); 4 Crooked gauges + NF Crooked reach (id 420). All wired,
  all fetching, all in the CSVs.
- **Reconciliation — byte-clean.** `test_migration_csv_reconciliation.py` → 3 passed; 32 migration-wired
  source names all in `source.csv`; content diff DB↔CSV = **0 rows either-only** across source/fetch_url/
  gauge_source; row counts match for all of source/gauge/gauge_source/fetch_url/reach/reach_state/reach_class.
  `levels orphan-check` → "No orphan sources." `check-reaches` → 420 reaches, 0 issues.

### 2. Data audit-trail

- **MED [audit-trail] — reach 118's HUC correction reached prod + the CSV with no SQL migration.**
  `git diff 22adc7c..HEAD -- data/db/reach.csv` shows reach 118 (`aw_10976`, Klamath) HUC
  `180102060303 → 180102060502`; the live DB agrees; the only commit touching it is the snapshot refresh
  8ce7366, and no migration anywhere wires it. `reach.huc` is **not** one of the two documented
  migration-exempt reach columns (CLAUDE.md exempts only `reach.geom` and `reach.gradient_profile`), and
  MEMORY's `feedback_migration_over_db_push` requires per-row reach backfills to go via migration for the
  audit trail. Data is consistent (a fresh `init-db`+import reproduces it from the CSV), but there's no
  migration recording *why* it changed. Footprint is exactly one pre-existing row. **Fix:** add an
  idempotent `UPDATE reach … WHERE aw_id=10976` migration, **or** document `reach.huc` as an
  `assign-huc`-derived snapshot-only column alongside geom/gradient (and update the convention sweep).

### 3. Doc drift (new, un-mechanized prose)

- **MED [drift] — `fetch-usgs-ogc` is documented as gauge-keyed; #75 made it source-keyed.** `CLAUDE.md:82`,
  `CLAUDE.md:171`, and `README.md:90` all say the OGC fetch runs "for gauges with `usgs_id`," but
  `fetch_usgs_ogc.py`'s own detailed docstring states selection "no longer keys on `gauge.usgs_id` … a
  merged gauge with `usgs_id` NULL still fetches its USGS sources" — exactly the Batch A Bonneville case.
  The doc now actively misleads about the new selection rule (and the module *header* docstring at
  `fetch_usgs_ogc.py:3` + `--site` help are internally stale too). **Fix:** reword to "for gauges linked to
  a USGS source"; fix the in-code header docstring in the same sweep.
- **MED [drift, curation-caveated] — `CHANGELOG.md` `[Unreleased]` is empty; the entire #73/#75–#83 batch
  is unrecorded.** Lines 9–11 show `## [Unreleased]` immediately followed by `## [1.2.0]`. Unrecorded:
  the source-based USGS-OGC refactor, Batch A/B/C (~20 reaches + ~10 gauges), the snapshot refresh, the
  map right-click feature. This trips no test by design — the file's stated policy is "curated and
  thematic," and the R2.2 guard deliberately checks *facts*, not *completeness* — so it is curation lag,
  not a false fact. The recurring "hygiene lags every merge" pattern, narrowed to the one surface policy
  exempts. **Fix:** add an `[Unreleased]` entry for the USGS-OGC refactor + Batch A/B/C.
- **LOW [drift] — `docs/PLAN_add_gauges_reaches.md:77–80,316`** carries the same "any gauge with `usgs_id`"
  framing, but as *add-a-gauge guidance* (still correct practice); only a reader inferring the *mechanism*
  is misled. Correctly placed in `docs/` root (Status: In progress; trips no `test_doc_plans_filed` check).

### 4. CI & test completeness (new work)

- **MED [coverage] — the #79 right-click map popup / Copy has no behavioral test.** `static/feature-map.js`
  gained a 58-line `contextmenu` handler; the only `/map.html` coverage (`tests/js/smoke.spec.ts:85`)
  asserts load + zero JS errors. A wrong-coords / popup-not-opening / clipboard-throws regression passes CI
  (a *syntax* error would surface via the smoke test; a logic error would not). **Fix:** a Playwright case
  firing `contextmenu` and asserting the `.latlon-popup` renders a coordinate string.
- **LOW [coverage] — committed Batch B/C reach geometry isn't validated at merge.** `check_reaches.scan_for_issues`
  runs over synthetic reaches in tests and over the real 420 only in the *pipeline* soft-fail (nightly on
  prod); no CI test loads the committed `reach.csv`+`reaches.json` and scans it. By design (geom is
  dev-only-regenerable), but the merge gate is blind to a malformed committed geom. **Fix (optional):**
  a test that imports the committed metadata into in-memory SQLite and asserts `scan_for_issues()` empty.
- **LOW [reuse] — `Makefile` `.PHONY` omits `test-php`** (and `init-db`/`install`/`help`), the `check:`
  dependency added by #81. Harmless today (no such file); a `test-php` file would make `make check` skip
  PHP tests. **Fix:** add them to `.PHONY`.
- **LOW [coverage] — the reconciliation guard only matches the `INSERT … SELECT` wiring form**
  (`test_migration_csv_reconciliation.py:38`). All 9 current wiring migrations use it, but a future
  `INSERT INTO source … VALUES ('NAME', …)` extracts zero names and silently bypasses the check.
  **Fix:** also capture the `VALUES ('<name>'` form, or assert no `INSERT INTO source` uses `VALUES`.
- **LOW [coverage, residual] — `ConfigTest::testEmitConfigJsonRoundTripsViaConfig` still *skips* (not
  fails) when `levels` is absent** (`ConfigTest.php:157`); it runs in CI only because `pip install -e .`
  happens to put `levels` on PATH — an implicit ordering dependency, not an assertion (the round-4 R4.1
  fix shipped, but the skip-as-invisible-pass shape remains). **Fix:** set `KAYAK_LEVELS_BIN` in CI and
  fail (not skip) when it's set but the binary is missing.

---

## Verified SOUND (re-checked against source — the durable wins)

- **Every round-4 *mechanized* guard held AND actually guards.** Each was re-read and run, and each has a
  non-vacuity self-test confirmed to fire: `test_schema_doc_sync.py` (forward + reverse + agency-enum off
  `source.csv`, 9 agencies matching DB + doc), `test_changelog_facts.py` (closed-ID-described-as-open),
  `test_doc_plans_filed.py` (done-plan-stranded-in-root; correctly leaves the new in-progress plan alone),
  `test_migration_csv_reconciliation.py` (R4.4), `check-phpstan-level.sh` (R2.1), `verify-systemd-units.sh`
  in CI (R4.2 — filter tolerates prod-path noise, catches a malformed `OnCalendar=`/`ExecStart=`). This is
  the round-4 thesis vindicated: the guards that were mechanized did not drift.
- **All six round-4 *doc* fixes stuck:** README/pre-commit "level 9" (no stale "level 8" current-claim),
  CHANGELOG facts, docs/done index + the moved gradient plan, mypy CI-scope = 3 scripts, ruff `C901` in
  CLAUDE.md+CONTRIBUTING, `.env.example` section anchor.
- **R1.4 (review-apply allowlist) — airtight + tested.** `reach_propose_fields.php` defines the shared
  `REACH_TEXT_FIELDS`+`REACH_FULL_FIELDS`; both `propose_handler.php` and `review_logic.php:135` consume
  it via `array_intersect_key` **before** building the `"$f = ?"` SET clause; `ReviewLogicFunctionalTest`
  proves forged `id`/`no_show` keys are dropped (logged) and legit lat/lon still applies. No drift possible
  — both sides read the same constant.
- **Python core — no findings.** R5.1 source_id tiebreak in *both* `order_by` blocks + the bulk SQL;
  R5.2/R5.5 migrate.py dup-prefix raise + `;`-in-literal guard (honors the SQLite `''` escape); R5.4
  `init-db --drop` drops `schema_migrations`; R5.7 gauge-cache window applied. USGS-OGC refactor correct.
  models↔migration lockstep exact (all 0061–0068 DML-only; `git diff` of `models.py` empty). Calculator
  topo-sort/placeholder, parser robustness, pipeline DAG + orphan/check-reaches soft-fail all sound.
  `pytest` 983 passed / 80% cov; `ruff`/`mypy` clean.
- **PHP security core — re-verified sound.** SQL fully parameterized (dynamic sites use placeholder-*count*
  interpolation, never value concat; the two SET-builders interpolate only allowlisted identifiers);
  output escaped; 32-byte `random_bytes` session/CSRF tokens stored sha256, double-submit with `hash_equals`;
  CSP `script-src 'self'`, no inline handlers; #79 popup uses `createElement`+`textContent` (no innerHTML),
  #80 svg change is a numeric guard (no interpolation). `composer test` 519 passed; `composer analyse`
  level 9 + strict, 0 errors.
- **Ops mechanization (the *other* half of round-4 ops) — A-tier and held.** R5.6 backups moved out of the
  repo *consistently* (both sync scripts, all 3 backup units' `ReadWritePaths=`, SETUP provisioning); R5.3
  root-unit hardening (`SystemCallFilter=@system-service` + scoped caps on both root units); R4.3 mime-extras
  in drift manifest + SETUP; R4.2 systemd-unit CI validation; #82 db_pull.sh bash-3.2-clean. `deploy.sh`
  applies migrations + `import_metadata --geom-only/--gradient-only` gated on the JSON changing — Batch B/C
  fire correctly, Batch A (no reaches) correctly doesn't. `shellcheck` clean at warning severity.
- **Data — byte-clean** (see §1). **Tracked-artifact bloat — none** (`git ls-files` finds zero
  Elevation-cache/Trace-cache/coverage.xml/pyc; the geom/gradient JSONs are intentional, with
  `.gitattributes` collapsing their diffs).

---

## The pattern, five rounds in

R1–R3 held flat at B−; round 4 was the first time improvement pulled ahead — *and round 5 shows that
lead was partly on paper.* The mechanized, committed, tested half of round 4 is genuinely durable: every
guard held, every guard still guards, the new data work is the cleanest yet. But the half that round 4
routed **"out-of-band on prod"** — its two ops HIGHs and the source_url security fix — never reached the
repo, and the round-4 review recorded them as closed and "verified durable" anyway. R1.4, the lone
Phase-1 item that shipped as a committed, tested PR, is the control case: it held perfectly.

So the round-4 thesis — *mechanization sticks; discipline doesn't* — now has its sharpest proof, drawn
from the prior round's own remediation: **the fixes that were committed-and-mechanized stuck without
exception; the fixes that bypassed that discipline (⏳ out-of-band, or quietly dropped) vanished without
exception.** And a structural lesson rides on top: in a `git pull --ff-only` deploy model where
`deploy.sh` refuses a dirty tree, "apply it by hand on prod" is not a stable state for a tracked file —
it is either reverted (re-arming the bug) or blocks deploys.

The highest-leverage round-5 move is to do to the *remediation record* what R3 did to the schema doc:
**mechanize a claim-vs-source guard.** Concretely — a test that parses each `docs/done/PLAN_round*_*.md`
header for items asserted shipped (the `Rx.y #NN` lines) and fails if the stated **Verify** command
doesn't actually pass against `HEAD` (e.g. R1.1's own `grep -c 'DELETE FROM pages' … → 0`). That one
guard would have caught all three non-landings the day round 4 was archived, and it closes the class —
no future review can record a fix as done while the source says otherwise. Land the three trivial Phase-1
fixes (delete a line, add a `trap`, add `\t` + a test) as committed PRs first; the guard close behind.
