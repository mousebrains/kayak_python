# Kayak — Deep Project Review (Round 6)

**Reviewed:** 2026-05-30 · branch `main` (live tree @ `db34ae0`), review on `review-6`
**Prior rounds:** R1 (05-23) **B−** → R2 (05-24) **B−** → R3 (05-25) **B−** → R4 (05-26) **B** → R5 (05-29) **B−**,
archived at `docs/done/REVIEW_round5_2026-05-29.md` (remediation `docs/done/PLAN_round5_remediation.md`, execution `docs/done/IMPL_round5.md`).
**Method:** 6 parallel cold facet auditors (Python core, PHP/security/frontend, schema/migrations/data,
tests/CI, ops/deploy/hardening, docs/hygiene/PR-discipline), each told to review *cold*, verify every
claim against committed source, and judge **two bands**: (A) did round-5's own fixes durably stick, and
(B) what did the new post-round-5 work introduce. **Every headline and every security finding was then
hand-re-verified by the synthesizer against committed source** (grep / `git log -S` pickaxe / file:line /
fresh-DB build / break-it experiments). The data facet built a clean DB from `data/db/*.csv` and ran the
real validators; the tests facet proved each round-5 guard non-vacuous by *breaking* what it guards and
watching it go red. Two facet over-claims were dissolved in synthesis (see convergence note).
**Scope:** entire tracked repo, with extra scrutiny on the round-5 remediation surface (#85–#91 — *this
round audits the prior round's own fixes, the recursive integrity check the series exists for*) and the
new feature/data batch (#93–#98 + migrations 0069/0070/0071 + the two nightly snapshots eb4a274/6e228d4 +
the two direct-to-`main` commits 9b428bb/6007c21).
**External review (PR #99, 2026-05-31):** an independent verification pass re-confirmed every finding,
severity, and the grade against `db34ae0` (recommendation: merge) and caught one inaccurate evidence line +
three off-by-one citations, corrected here: the MED-#1 `git branch --contains` claim was dropped (feature
branches later cut from `main` now contain those commits, so containment no longer distinguishes them — the
linear-history + missing-`(#NN)` evidence is the durable proof); `ci.yml:114→115`, `SourceUrlTest.php:83-84→84-85`,
`check_reaches.py:212→213`. It also noted one below-LOW item the audit didn't call: the 0069/0070 migration
*header comments* still say JDA/BON/VAPW1/SHNO3 are "in `PENDING_RECONCILIATION` until the snapshot lands them,"
now stale (the snapshot landed them; the set is empty) — the same stale-comment class as the CHANGELOG LOW.

---

## Executive summary

**For the first time in the series, the prior round's remediation fully held — and the very next batch
re-opened the one class round 5 never mechanized.** Both halves are true, and together they make round 6
the cleanest *proof* of the standing thesis the project has produced.

The bright half is the brightest yet. The recursive integrity check — *did round-5's fixes durably
stick?* — **passes cleanly on every item**, the first round where it has. R1.1 (`DELETE FROM pages`
deleted), R1.2 (the timer-restart `trap … EXIT`), R1.3 (the `source_url` TAB filter), R1.5 (mktemp), and
the R2.1 claim-vs-source lever all landed as committed PRs and are still present at `db34ae0` — confirmed
by pickaxe and by running the guards. And the round-5 *mechanized* guards were proven **non-vacuous by
break-it experiment**: re-adding `DELETE FROM pages` turns `test_remediation_claims.py` red; neutering the
`contextmenu` listener turns the Playwright map-popup spec red; WKT-wrapping a committed geom turns
`test_committed_reach_geom.py` red; removing the `db_push.sh` trap turns `check-db-push-trap.sh` red. The
new code batch matches: #93's USACE kcfs→cfs scaling is correct, per-series, and leaves the Willamette
dams untouched; migrations 0069/0070/0071 are idempotent over three re-runs, FK-clean
(`PRAGMA foreign_key_check` empty), and the Bridgeport DROP cascade is **provably residue-free** against
the models.py FK clauses; orphan-check and check-reaches are green; the multi-state border-gauge picker
work (#96/#97) is parameterized with a comma-wrapped `INSTR` match that resists the `'OR' ⊂ 'OREGON'` trap;
the gradient-profile JS (#95/#98) is CSP-safe and its hover-dot↔geom-trace coordinate mapping is correct.
983-ish Python tests, 524 PHP tests (PHPStan level 9 + strict clean), and the Playwright spec all pass.

The dark half is small, already self-corrected, and *thematically loud*. Two MED findings, **both
discipline-class recurrences of round-5 items that round 5 closed by documentation or never mechanized**:

- **The PR/worktree workflow was bypassed twice on `main`.** `9b428bb` ("Drop new USACE gauges") was a
  direct-to-`main` push with no PR, no conventional prefix, an empty body, and a **misleading subject** — it
  drops no gauges; it only edits one test, emptying `PENDING_RECONCILIATION` to `{}` (an empty *dict*, not
  a set). The `set & dict` TypeError **broke CI on `main`** (#813, both 3.13 and 3.14) for 77 minutes until a
  *second* direct-to-`main` commit `6007c21` fixed it to `set()`. The HEAD state is correct; the wound was
  transient — but it landed in the exact class round 5's whole grade was about ("out-of-band changes
  evaporate or cause harm"), and **nothing in the repo mechanically prevents either direct-to-`main` or a
  misleading subject** (no branch-protection-as-code, no commit-msg lint).
- **A snapshot silently overrode a migration's explicit intent with no audit trail.** Nightly snapshot
  `eb4a274` changed gauge 217's `sort_name` from `lewis|0canyon|005000|000000` (set deliberately by
  migration 0067 to group Canyon Creek under the Lewis basin) to `canyon creek (lewis river trib.)|9|999999|999999`
  — the exact value `seed_gauge_display.py` computes — moving the gauge out of its intended group. No
  migration records the change. This is round-5's R3.1 audit-trail finding (reach 118's HUC) **recurring on
  a different column** — and on a column round 5 did *not* bless as snapshot-carried.

### Overall grade: **B+ (▲ from B−)** — a recovery, and the strongest code+verification state in the series

Round 6 reverses the round-5 dip and clears the round-4 B. The path of the grade is the story: round 5
fell to B− because the prior round's record was *false*; round 6 finds that record *true* and durable, the
new code clean, and every guard load-bearing — so the verification-integrity penalty lifts. It stops short
of A− for one reason only: the un-mechanized discipline gap is still open, and it didn't just sit there —
it **recurred twice in the review window** (a CI-breaking direct-to-`main` push and a migration-overriding
snapshot). The lesson is now proven a third way: *the half that was mechanized held perfectly; the half
left to discipline failed again.* The lever is the same move round 5 made for claim-vs-source — mechanize
the gate (branch protection + a snapshot-column guard), and the grade has a clean path to A−.

| Axis | Grade | Δ | One-line |
|---|---|---|---|
| Self-consistency | **B+** | ▲ from C+ | No false records this round — every Band-A claim verified true at HEAD; the lone self-inconsistency is `9b428bb`'s subject vs. its diff. |
| Drift (docs/config vs reality) | **B+** | ▲ from B | All four R3.x doc fixes stuck; parser docstrings updated in lockstep with #93; only `CHANGELOG [Unreleased]` lags the batch. |
| New-maintainer docs | **B+** | ▲ from B | Schema/exception notes intact; `PLAN_add_gauges_reaches.md` gains a clean "Shape 4 — dropping a gauge" recipe. |
| Ops / deploy | **B+** | ▲ from C+ | R1.1/R1.2 landed as committed, CI-locked, break-it-proven guards; new migration deploy is atomic + self-healing. |
| Security | **A−** | ▲ from B | R1.3 TAB filter landed *with* regression tests (pickaxe-confirmed); new multi-state SQL parameterized; no XSS/CSP regression. |
| Data consistency | **B+** | ▼ from A− | Bridgeport removal + 4 new sources reconcile byte-clean; one un-audited snapshot edit (gauge 217 `sort_name`) rode in. |
| Test coverage of recent work | **A−** | = | Every round-5 guard proven non-vacuous by break-it; #93/#96/#97 tested; gaps are LOW (untested #98 hover, mypy excludes `tests/`). |

### Per-dimension grades (Δ vs. R5)

| Dimension | Grade | Δ |
|---|---|---|
| Python package core (`src/kayak/`) | **A−** | = (no findings; #93 correct + tested; lockstep exact) |
| Schema & migration consistency | **A−** | = (0069/0070/0071 idempotent + FK-clean; DROP cascade residue-free) |
| Data & config consistency | **B+** | ▼ from A− (one un-audited snapshot edit: gauge 217 `sort_name`) |
| Documentation & onboarding | **B** | = (Band-A fixes stuck; new work well self-documented; CHANGELOG lag) |
| CI & testing infra | **A−** | = (guards proven non-vacuous by break-it; new work mostly tested) |
| PHP & frontend | **A−** | ▲ from B+ (multi-state work parameterized + CSP-safe; coordinate mapping correct) |
| Ops / deploy | **B+** | ▲ from C+ (the two non-landed HIGHs now landed + CI-locked; new deploy sound) |
| Security | **A−** | ▲ from B (R1.3 landed + tested; auth/CSRF/CSP/SQL core re-verified sound) |
| PR discipline / record integrity | **C** | = (false-record class gone, but two direct-to-`main` commits replace it; no mechanical guard) |
| Tracked-artifact bloat | **A−** | = (zero bloat; geom/gradient JSON snapshots intentional, `.gitattributes`-collapsed) |

---

## Findings by theme

Severity: **CRIT** / **HIGH** / **MED** / **LOW**. Evidence is `file:line` / migration / commit / query.
**Every headline + every security item was hand-re-verified against committed source.** No CRIT and no
HIGH this round: the two ops HIGHs that pinned round 5 are landed, and the one transient CI break was
already self-corrected (HEAD is clean). The round therefore leads with *process*, not a live defect.

**Convergence note (independent corroboration + two dissolved over-claims):** the direct-to-`main`
discipline finding was surfaced **independently by three facets** (Python, tests/CI, docs) — corroboration,
not coordination — though they split on severity (LOW / HIGH / MED). The synthesizer lands it at **MED**:
the concrete impact was bounded and already fixed (calibrating against round 5, whose "HIGHs" were *armed*
latent bugs, not an already-corrected transient), but it is more than LOW because it is a thesis-class
recurrence with no guard. Two facet leads were **dissolved on hand-re-verification**, and both are recorded
so the trend stays trustworthy: (1) the Python facet's seeded lead — #93's class docstring dropping
"temperature" — was proven a *correct doc fix*, not a silent loss: `git log --all -S'Temp-Water' -- …usace_cda.py`
shows the parser never mapped temperature; an early wiring branch (`0341337`) did wire USACE water-temp
and a deliberate review decision (`9ce1be3`) trimmed it to flow-only with sound rationale (the dam temps
duplicate the gauges' existing USGS temperature). (2) The tests/CI facet's LOW — "out-of-range geom
vertices are only caught via endpoint drift, so a NULL-endpoint reach passes" — was **refuted**:
`parse_geom_string` (`tracing/format.py:105`) calls `validate_lat_lon` **per vertex** (`format.py:36`,
strict `[-90,90]`/`[-180,180]`) *before* the drift checks, so an out-of-range vertex raises and
`check_reaches.py:213` flags it "geom unparseable" regardless of the endpoint columns (verified empirically:
`-122 45,-121 999` → `latitude 999.0 out of range`). The docstring's out-of-range check is accurate; the
facet's break-it geom string had failed the *arity* check, not the range check.

### 0. The recursive integrity check — round-5's remediation, audited cold

> **Verdict on the round-5 remediation:** *all of it held.* This is the first round where the prior round's
> fixes durably stuck without exception — the inverse of round 5's finding against round 4. Recorded here as
> **verified**, not as findings.

| Round-5 item | Verify (re-run at HEAD) | Result | Durable? |
|---|---|---|---|
| **R1.1** delete `db_push.sh` `DELETE FROM pages` | line-count → 0 | **0**; `git log -S` → removed in `12074b3` (#85), absent at HEAD | ✅ |
| **R1.2** timer-restart `trap … EXIT` | trap armed between stop (`:100`) & clean restart (`:187`) | present `db_push.sh:121`; full coverage traced — no exit path strands the 4 timers | ✅ |
| **R1.2 guard** `check-db-push-trap.sh` | fires on trap removal | **fires** (exit 1); wired `ci.yml:115`; shellcheck-clean (trap opaque in heredoc) | ✅ |
| **R1.3** `source_url` TAB filter | `/[\r\n\t\0]/` present + tab tests | `source_url.php:34`; `git log -S'\r\n\t\0'` → single hit `18870c4` (#86); `SourceUrlTest.php:84-85` tab cases pass | ✅ |
| **R1.5** mktemp the `/tmp` paths | `$(mktemp)` not predictable paths | `db_push.sh` `LIVE_FINAL/NEW_DB="$(mktemp)"`; `.backup`/`gunzip >` targets updated | ✅ |
| **R2.1** claim-vs-source lever | passes at HEAD + non-vacuous | `test_remediation_claims.py` → **3 passed**; re-adding `DELETE FROM pages` → **RED** (break-it) | ✅ |
| **R3.1–R3.4** doc fixes | grep clean / erratum present | R3.2 grep returns nothing; R3.1 reach.huc note, R3.3 CHANGELOG, R3.4 round-4 erratum all present | ✅ |
| **R4.1–R4.5** CI/test hardening | each runs + non-vacuous | map-popup spec, committed-geom test, `.PHONY`, VALUES-form capture, ConfigTest fail-not-skip — all confirmed | ✅ |

The R2.1 lever has *grown* coverage exactly as designed: it now enforces **2** runnable Verify lines (round
5 reported 1), because round-5's own plan — now archived in `docs/done/` and matching the guard's glob —
references the same R1.1 check, and the three documented self-reference traps (in-flight glob, BRE
metacharacter, quote-scoped attempt counter) all correctly *don't* fire on its 67 parsed `**Verify:**` fields.

### 1. Process & audit-trail — the two MED findings (both class recurrences)

- **MED [process / record integrity — recurrence of the round-5 "out-of-band" class] — two direct-to-`main`
  commits bypassed the PR/worktree workflow; the first broke CI with a misleading subject.** Evidence:
  both are **direct commits on `main`** — `f3ed673..HEAD` is linear (no merge commits), and every other commit
  in the range carries a `(#NN)` squash-merge suffix (the PR marker) that both `9b428bb` and `6007c21` lack,
  so they reached `main` without a PR. `9b428bb` "Drop new USACE gauges" touches
  **only** `tests/test_scripts/test_migration_csv_reconciliation.py` (it drops no gauges — they were already
  wired by #93/#94 and landed in `source.csv` by snapshot `6e228d4`); it has no conventional `type:` prefix
  and an empty body (against MEMORY `feedback_commit_msg_style`); and it wrote `PENDING_RECONCILIATION: set[str] = {}`
  — `{}` is a *dict*, so `_csv_source_names() & PENDING_RECONCILIATION` raised `TypeError: unsupported operand …
  'set' and 'dict'` on **`main`** (CI #813, `test (3.13)` + `test (3.14)`), fixed 77 min later by the *also*
  direct-to-`main` `6007c21`. `mypy` flags `set[str] = {}` as an assignment error — but CI's mypy scope
  (`ci.yml`, `src/` + 3 scripts) **excludes `tests/`**, and ruff (which does lint `tests/`) doesn't type-check,
  so the annotation-lie had no static gate. The HEAD state is correct (`set()`); the impact was a 77-min
  red `main` with no data/security consequence. **Why it's the headline:** the standing thesis is "mechanization
  sticks; out-of-band discipline doesn't," and this is the *harm* variant recurring in the very next batch —
  and there is **no branch-protection-as-code, no commit-msg lint, and nothing in `.pre-commit-config.yaml`**
  (which carries only content linters) that makes either direct-to-`main` or a misleading subject impossible.
  **Fix:** (1) require a passing-CI PR to merge to `main` (GitHub branch protection — the load-bearing lever;
  a local hook can't stop a `git push`); (2) add `tests/` (or at least `tests/test_scripts/`) to the mypy CI
  line — a one-line change that statically catches the `{}` class pre-merge; (3) optional `commit-msg`
  commitlint for the subject convention.

- **MED [data / audit-trail — recurrence of round-5 R3.1] — a nightly snapshot overrode migration 0067's
  explicit `sort_name` for gauge 217 with no migration.** Evidence: `git log -p f3ed673..HEAD -- data/db/gauge.csv`
  shows gauge 217 (USGS 14219000, Canyon Creek near Amboy) `sort_name`
  `lewis|0canyon|005000|000000` → `canyon creek (lewis river trib.)|9|999999|999999`, carried by snapshot
  `eb4a274` ("metadata snapshot — gauge"). Migration `0067_wire_lewis_reaches.sql:57` *deliberately* set
  `'lewis|0canyon|005000|000000'` ("the Sandy-basin convention 'Lewis … NN'") so Canyon Creek groups under
  the Lewis basin on `gauges.html`; the new value is exactly what `scripts/seed_gauge_display.py::build_sort_name`
  computes for this gauge (river `Canyon Creek (Lewis River trib.)`, NULL elevation/DA → `|9|999999|999999`),
  so `seed_gauge_display.py` was run on prod over this row and the snapshot carried it back — overriding the
  migration's intent and **moving the gauge out of its Lewis group into its own "Canyon Creek" group** (it
  backs reaches 418/419). `gauge.sort_name` is **not** a documented snapshot-carried exception (CLAUDE.md:207
  exempts only geom/gradient/huc), and 0070's own comment (`:27`) warns "do NOT run seed_gauge_display.py on
  these — it would clobber the order." The data reconciles (DB == CSV; a fresh rebuild reproduces it), so this
  is audit-trail + a minor display regression, not an integrity break — footprint exactly one pre-existing row.
  **Fix:** either (a) an idempotent `UPDATE gauge SET sort_name='lewis|0canyon|005000|000000' WHERE name='14219000'`
  migration to restore 0067's intent (and make a from-scratch rebuild match prod), or (b) if the new grouping
  is actually wanted, record it in a migration; and — the durable move — **mechanize a guard** that fails if a
  snapshot changes a non-exempt `gauge`/`reach` column with no migration wiring it (the same shape as the
  reconciliation guard), which would close this whole class rather than the specific row.

### 2. New code & data — verified clean (the bright spot)

> Audited cold against source, a fresh CSV-built DB, and a migration-execution DB. Recorded as *verified*,
> not as findings — this is the cleanest combined code+data batch the series has seen.

- **#93 USACE kcfs→cfs (`parsers/usace_cda.py`) — correct.** Per-series `units` read at `:82`, scaled
  `*= 1000.0` iff `units == "kcfs"` at the `_entry_to_record` leaf (`:113`) — cannot double-scale (one multiply
  per record), tolerant of missing/odd units (empty → no scale), and the 4 wired URLs request only `Flow`
  series so the theoretical "elevation in kcfs" mis-scale is unreachable. Willamette dams (cfs) untouched.
  New tests are real (no-double-scale regression `4110.0 → 4110.0`; whitespace/case; full parse→DB round-trip).
- **Migrations 0069/0070/0071 — idempotent, FK-clean, lockstep-exact.** Verified over three re-runs (exactly
  one each of JDA/BON/VAPW1/SHNO3 + their fetch_url/gauge_source). 0071's Bridgeport DROP cascade verified
  against models.py: `observation.source_id` is `ondelete="RESTRICT"` (`:359`) so the explicit obs-delete-first
  is required and correct; `gauge_source.source_id` (`:224`), `latest_observation.source_id` (`:392`),
  `latest_gauge_observation.gauge_id` (`:420`) all CASCADE — **post-drop residue sweep: 0 rows across every
  related table; `PRAGMA foreign_key_check` empty.** `git diff f3ed673..HEAD -- src/kayak/db/models.py` is empty
  and 0069–0071 are DML-only, so the empty models diff is correct (no masked drift).
- **Reconciliation — byte-clean for the named rows.** Bridgeport `12438000` absent from source/gauge/gauge_source/fetch_url
  CSVs and removed cleanly by 0071; JDA/BON/VAPW1/SHNO3 in `source.csv:343-346` with correct fetch_url links;
  the R4.4 `_deleted_sources()` regex extracts `12438000` from 0071 so the guard doesn't wrongly expect it.
  `PENDING_RECONCILIATION` genuinely empty (`set()`). Fresh CSV-built DB: `levels orphan-check` → "No orphan
  sources."; `levels check-reaches` → "checked 420 reaches; 0 with issues." (The one CSV edit *not* covered by
  this clean story is gauge 217 — §1.)
- **#96/#97 multi-state pickers — parameterized + correct.** `gauge_picker.php:56` builds
  `INSTR(',' || g.state || ',', ',' || ? || ',') > 0` joined by `OR`, one `?` per abbrev, executed with the
  fixed abbrev list — the comma-wrapping defeats `'OR' ⊂ 'OREGON'` (adversarially tested: matches `OR,WA`/`WA,OR`/`CALIFORNIA,OR`,
  rejects `OREGON`/`FOR`). `custom_gauges_handler.php` changes are render/filter only, allowlist-gated,
  `htmlspecialchars`-escaped. Real PHP integration tests landed (4 tests / 14 assertions).
- **#95/#98 gradient-profile JS — CSP-safe + coordinate-correct.** DOM via `createElementNS`/`textContent`/Leaflet
  `circleMarker` (no `innerHTML`); `data-track` is `htmlspecialchars(json_encode(...))`, `JSON.parse`d in
  try/catch with an `Array.isArray && length≥2` guard. #98's hover-dot↔geom mapping is correct: both axes are
  parameterized as distance-from-put-in in river-miles (`svg_plot.php:457`, `x_min=0`/`x_max=length_mi`) vs.
  the geom's cumulative haversine arc-length, so `f=(dMi-xMin)/span`, `target=f*total` maps 1:1 — no off-by-one,
  endpoint-clamped before a `lo+1<hi` binary search.

### 3. Lower-severity findings

- **LOW [docs] — `CHANGELOG.md` `[Unreleased]` omits the entire round-6 batch.** `git log f3ed673..HEAD -- CHANGELOG.md`
  is only the round-5 R3.3 edit; no entry for the kcfs→cfs **units bugfix** (changes displayed lower-Columbia
  flow 1000×, the most user-visible item), JDA/BON/VAPW1/SHNO3 new gauges, the Bridgeport drop, or the picker
  fixes. By-design uncaught (the file's policy is "curated and thematic"; `test_changelog_facts.py` checks
  facts, not completeness), so it's lag, not a false fact. **Fix:** a 4-line Fixed/Added/Removed `[Unreleased]`
  entry, especially the 1000× units fix.
- **LOW [coverage] — `static/gradient-profile.js` #98 (75 lines of hover-dot logic) has no behavioral test.**
  Testable via a Playwright dot-position assertion (unlike #95, which is CSS-only and genuinely presentational).
  **Fix (optional):** a small hover-dot Playwright case, or accept as low-risk presentation glue.
- **LOW [coverage] — the `PENDING_RECONCILIATION` stale-allowlist test is currently dormant.** With the set
  empty, `_csv_source_names() & set()` is always `∅`, so `test_pending_reconciliation_allowlist_is_not_stale`
  passes trivially today. Correct (nothing is pending; the other reconciliation tests do the real work) — noted
  only so the next author knows its guard value is zero until something is added to the allowlist.
- **LOW [deploy, self-healing] — `deploy.sh` doesn't pause `kayak-pipeline.timer` during a deploy.** A fetch
  firing in the gap between `git pull` and `levels migrate` would hit the new JDA/BON URL (present in both
  `sources.yaml` and 0069) before the source rows exist, auto-creating a transient orphan and one orphan-check
  email. **Not the 1000× bug:** #93 (parser) is an ancestor of #94 (`git merge-base --is-ancestor` confirmed),
  so the single atomic `git pull` lands the new parser with the new URL — any racing fetch runs the *new* parser
  and stores correct cfs, and 0069's `NOT EXISTS`/`INSERT OR IGNORE` self-heals the row on migrate. **Fix
  (optional hardening):** bracket pull→migrate with `systemctl stop/start kayak-pipeline.timer`.
- **LOW [doc] — migration 0069's "DEPLOY ORDER: … parser … must be deployed FIRST" header is misleading.**
  The ordering is guaranteed by commit topology (parser is an ancestor), not by an operator step; the note
  invites a manual ordering that this `git pull --ff-only` model structurally prevents. **Fix:** reword to
  "guaranteed by commit ordering — #93 is an ancestor of this migration's PR."

---

## Verified SOUND (re-checked against source — the durable wins)

- **The entire round-5 remediation held (§0).** First clean recursive integrity pass in the series. The two
  ops HIGHs that pinned round 5 are landed *and* CI-locked; the security MED (R1.3) landed *with* tests; the
  R2.1 lever passes and is break-it-proven.
- **Every round-5 *mechanized* guard is non-vacuous** — proven by breaking what it guards and watching red:
  `test_remediation_claims.py` (re-add `DELETE FROM pages`), the map-popup Playwright spec (neuter
  `contextmenu`), `test_committed_reach_geom.py` (WKT-wrap a geom), `check-db-push-trap.sh` (remove the trap),
  the reconciliation guard (remove GPRO3 from `source.csv`). The R4.5 ConfigTest now *fails* (not skips) under
  a CI-set `KAYAK_LEVELS_BIN` (`ci.yml` resolves `levels` and `exit 1`s if absent).
- **Python core — no findings.** #93 correct + tested; multi-state split in `web/build/gauges.py:291` works
  (`OR,WA` → `data-state="Oregon,Washington"` → two pills) and `sort_name` is read, never recomputed; R5.1/R5.2/R5.4/R5.5/R5.7
  fixes all present at HEAD; models↔migration lockstep exact; ruff/mypy clean on changed files.
- **PHP security core — re-verified sound.** All new SQL parameterized (placeholder-*count* IN-clauses, never
  value concat); no `mb_*` (prod lacks mbstring); output `htmlspecialchars`-escaped; CSP `script-src 'self'`
  intact; R1.4 review-apply allowlist still consumed via `array_intersect_key` before the SET build (no drift —
  both sides read the same constant); auth/session/CSRF code untouched this round, tests green. `composer test`
  524 passed; `composer analyse` level 9 + strict, 0 errors.
- **Schema/data — DROP cascade residue-free, migrations idempotent, orphan/reach-clean** (see §2). 0071's
  pre-flight assertions all true (no calc input, no `reach.gauge_id`, no `sources.yaml`/`src` reference to
  `12438000`). The 0066-add→0071-drop Bridgeport churn is clean in both DB and CSV.
- **Ops mechanization — A-tier and held.** R5.6 backups out-of-repo (all units' `ReadWritePaths` carry zero
  repo paths), R5.3 root-unit hardening (`SystemCallFilter=@system-service` + empty `CapabilityBoundingSet`),
  R4.2 `verify-systemd-units.sh` in CI, #82 `db_pull.sh` bash-3.2-clean. `deploy.sh` applies migrations +
  `import_metadata --geom-only/--gradient-only` gated on the JSON changing; 0071 runs inside `engine.begin()`
  (atomic). shellcheck clean at warning severity.
- **Docs/onboarding — Band-A fixes durable + new work self-documented.** R3.1–R3.4 all stuck (R3.2 grep
  returns nothing); `PLAN_add_gauges_reaches.md` gains a clean "Shape 4 — dropping a gauge" recipe (delete-by-name
  tied to the reconciliation guard, FK-order template, Bridgeport worked example, next-prefix → 0072); USACE
  parser docstrings updated in lockstep with #93; CLAUDE.md "24 tables / 25 with schema_migrations" still correct
  (0069–0071 add none); round-5 archival complete (`docs/done/README.md:37-39`), nothing stranded in `docs/` root.
- **Tracked-artifact bloat — none.** `git diff --diff-filter=A f3ed673..HEAD` adds only `.sql`/`.md`/`.sh`/`.ts`/`.py`;
  `git ls-files` finds zero cache/coverage/pyc/binary; the geom/gradient JSONs are intentional and untouched this round.

---

## The pattern, six rounds in

R1–R3 held at B−; round 4 reached B on mechanization that round 5 proved was *partly on paper* (three
Phase-1 fixes recorded done had never landed), dropping to B−. Round 6 is the resolution: **the half round 5
committed-and-mechanized stuck without exception** — every guard present, every guard still firing — and the
new code is the cleanest the series has audited. The recursive integrity check, which has been the series'
sharpest instrument, returns *all green* for the first time.

And the thesis gets its third and cleanest proof, this time as a controlled experiment the project ran on
itself. In the same window where the mechanized guards all held, the **un-mechanized** discipline failed
twice — a direct-to-`main` push that broke CI, and a snapshot that overrode a migration — and *both* are
recurrences of round-5 items that were closed by **documentation or left un-mechanized** (the R3.1 audit-trail
class, and the "out-of-band changes" class). Mechanize a class and it stays closed across rounds; document a
class and it reopens. Six rounds of evidence now say the same thing.

So the highest-leverage round-6 move is the exact move round 5 made for claim-vs-source — **mechanize the
gate**, in two places:

1. **Branch protection on `main`** (require a passing-CI PR to merge) + add `tests/` to the mypy CI scope.
   Together these make both halves of the `9b428bb` incident impossible: the direct push is rejected, and the
   `{}`-is-a-dict annotation-lie is caught statically pre-merge. A local hook can't stop a `git push`; the
   server-side rule is the load-bearing one. **Caveat (this is also why #1 and #2 are one problem):**
   `kayak-metadata-snapshot.service` is *itself* an un-CI-gated direct-to-`main` pusher — `snapshot_metadata.sh:77`
   commits the nightly CSV dump straight to `origin/main` — so branch protection must *route the snapshot
   through the same gate* (an auto-merging PR, or a self-gating bot that runs the reconciliation + drift guards
   pre-push and aborts on red), not blanket-block direct pushes (which would strand the nightly snapshot).
2. **A snapshot-column guard** — a test that fails if a nightly snapshot changes a non-exempt `gauge`/`reach`
   column (anything but geom/gradient/huc) with no migration wiring it. The same shape as the reconciliation
   guard; it would have caught gauge 217 the day the snapshot landed. The drift's *source* is
   `seed_gauge_display.py --apply`, which recomputes `sort_name` for **all** gauges (`seed_gauge_display.py:335-342,357`
   — no WHERE filter, no migration-override skip), so a one-time restore migration is necessary but **not
   sufficient**: the tool must also *preserve migration-pinned `sort_name`s*, enforcing migration 0070's prose
   "do NOT run seed_gauge_display.py on these" in code — otherwise the next broad `--apply` re-clobbers gauge
   217 and the next snapshot mirrors it again. (`export_metadata.py` is a pure dump, so the snapshot itself
   never *originates* the drift — it faithfully re-exports whatever the prod DB holds, which is why a DB-side
   restore converges and a tool-side guard is the durable fix.)

Land the two trivial corrections first (the gauge-217 restore migration + the CHANGELOG/0069-header edits),
then build the two guards behind them — and the discipline class that has now recurred twice is closed the
way the claim-vs-source class was: mechanically, for good.
