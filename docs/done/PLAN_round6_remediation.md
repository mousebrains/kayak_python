# Round-6 Remediation Plan

> **SUPERSEDED by [`PLAN_metadata_single_source.md`](PLAN_metadata_single_source.md) (v2).** The
> maintainer pivoted from this duality-based remediation to the metadata-single-source redesign. Kept for
> provenance — this is the v1→v5 red-team iteration record that converged on the pivot.

**Source:** [`REVIEW_round6_2026-05-30.md`](REVIEW_round6_2026-05-30.md) (graded **B+**, ▲ from B−).
**Branch:** `review-6`.

> Remediates the round-6 review. Findings were cold-audited by 6 facet auditors, hand-re-verified against
> committed `HEAD`, and **this plan is iterated past a maintainer red-team pass + three 3-lens cold red-team
> passes against source** (see the version log). **v4 adopted a structural decision** (maintainer): split the
> machine-generated metadata snapshots into a **dedicated data repository** (`kayak-data`). Every fix lands as
> a CI-gated PR carrying a committed, non-vacuous guard.

**Standing thesis (rounds 1–6):** *durable guards that make a class of drift impossible beat one-off
corrections.* Round 6 proved it a third way: every mechanized round-5 guard held; the two new wounds both
entered through the **one un-CI-gated path to `main`**. Pass-2 red-team found *why that path can't be gated
in place:* the nightly snapshot bot pushes **as the repo owner** on a **personal repo** (`deploy/SETUP.md:51`),
so on a single-owner repo no branch-protection config distinguishes the bot from a human at a shell.
**The structural fix is to stop sharing one `main` between reviewed code and the unreviewable data bot:**
move the snapshots to their own repo. Then the code repo enforces clean admin-protection (humans → PRs, no
bot exception), the bot pushes only to `kayak-data`, and the data churn + JSON bloat leave the reviewed
history. This is a strategic refactor beyond the literal round-6 debt (gauge 217 + a CHANGELOG line) — adopted
because it closes the #1 class *durably* and de-tangles #2.

**Execution:** one PR per phase (phases may split), in the `review-6` worktree (never the live tree). Legend:
effort **S/M/L**, risk low/med/high. **Verify** lines feed the round-5 R2.1 claim-vs-source guard once
archived. **Archival ordering:** archive to `docs/done/` **only after Phase 1 lands** (the round-5 guard runs
this plan's grep Verifies against `HEAD`; they pass only post-fix). Grep Verifies use the guard's parseable
form (literal patterns, no `. * [ ] ^ $ \`; `|` is literal).

---

## Phase 1 — The actual round-6 debt (small, split-independent, ships first as one tiny PR)

> Pass-3-confirmed: gauge-217 + CHANGELOG + 0069-header + the seed_gauge_display fix can ship **today**, before
> any repo split, closing every review finding except branch protection (inherently the strategic part).

- **R1.1 [MED→fix] — restore gauge 217's deliberate `sort_name`: migration 0072 **and** the CSV.** Snapshot
  `eb4a274` overrode `0067_wire_lewis_reaches.sql:57`'s deliberate `lewis|0canyon|005000|000000` (groups Canyon
  Creek under Lewis; `build_sort_name` computes `canyon creek (lewis river trib.)|9|999999|999999`, the
  clobbered value; the `|`-prefix changed `lewis`→`canyon creek…` = a group move = the regression marker).
  **Both edits are required** (pass-3 correctness find): `levels init-db` **stamps** migrations as applied
  without running them (`init_db.py:217-220` → `migrate.py:101-132`), and `import_metadata.py` loads
  `gauge.csv` as the rebuild source-of-truth (upsert `sort_name = excluded.sort_name`) — so on a from-scratch
  rebuild 0072 never fires and the **CSV value wins**. Today `gauge.csv` row 217 still holds the clobbered
  value. **Fix:** (a) idempotent `data/db/migrations/0072_restore_canyon_creek_sort_name.sql`:
  `UPDATE gauge SET sort_name = 'lewis|0canyon|005000|000000' WHERE name = '14219000';` (fixes the *existing
  prod* DB on the next `levels migrate`; gauge.name UNIQUE, `models.py:122`); **and** (b) hand-edit
  `data/db/gauge.csv` row 217 to `lewis|0canyon|005000|000000` (fixes every rebuild/CI/byte-for-byte path).
  The two target different consumers (live prod DB vs. rebuild snapshot), so both are needed, not redundant —
  the migration supplies the audit trail the round-6 finding wanted; the CSV edit keeps rebuild==prod. 0072 is
  the next free prefix (`PLAN_add_gauges_reaches.md:481`; re-confirm vs open branches — MEMORY
  `migration_number_collision`). **Verify:** `grep -c 'lewis|0canyon|005000|000000' data/db/migrations/0072_restore_canyon_creek_sort_name.sql` → `1`
  **and** `grep -c 'lewis|0canyon|005000|000000' data/db/gauge.csv` → `1`; a fresh `init-db`+`import_metadata`
  (no migrate) yields the deliberate value for 14219000. **(S, low.)**

- **R1.2 [MED→fix] — make `seed_gauge_display.py` clobber-safe (fill-only by default, `--gauge` for refresh).**
  The tool selects all gauges (`:335-342`, no WHERE) and unconditionally recomputes the four display columns
  on `--apply` (`:391-398`: `SET river=?, location=?, display_name=?, sort_name=?`), so a broad run clobbers
  *every* deliberate override (~18 NULL-elevation river-mile/basin groupings would compute to `…|9|999999|999999`).
  **Fix (proportional — chosen over an 18-row registry):** (a) add the current display columns to the SELECT
  (`:335-342` lacks them) so apply can see existing values; (b) default `--apply` to **per-column fill-only** —
  write each of the four columns only where that column `IS NULL`; (c) add **`--gauge <name>`** to recompute a
  named gauge (the legit elevation/DA-refresh path) and **`--force`** to recompute all (with a printed warning
  that it overwrites deliberate overrides). Per-column fill-only protects every set value uniformly (no
  enumeration to keep complete); `--gauge` gives targeted refresh **without** the all-clobber `--force` (the
  pass-3 footgun: `--force` alone re-opens the wound). Retires `0070:26-28`'s prose "do NOT run … on these."
  **Verify:** `--apply` over a DB with set display values leaves them unchanged; a NULL-`sort_name` new gauge is
  still seeded; `--gauge 14330000` recomputes only that row; `--force` recomputes all. A unit test covers
  fill-only-skips-set + `--gauge`-targets-one + `--force`-overwrites. **(M, low.)**

- **R1.3 [LOW] — CHANGELOG entry + de-mislead the 0069 header.** Add a thematic `[Unreleased]` entry —
  **Fixed:** USACE lower-Columbia flow scaled kcfs→cfs (1000× small); **Added:** JDA/BON outflow + VAPW1/SHNO3
  stage gauges; **Removed:** Bridgeport (12438000) — prose only, no shipped-ID-paired-with-open-word
  (`test_changelog_facts.py`). Reword 0069's "DEPLOY ORDER … must be deployed FIRST" → "guaranteed by commit
  ordering — #93 is an ancestor of #94's PR." **Verify:**
  `grep -c 'DEPLOY ORDER' data/db/migrations/0069_wire_usace_columbia_dam_outflow.sql` → `0`; a content grep
  for `kcfs` in CHANGELOG `[Unreleased]` is non-empty (the section already has round-5 content, so
  non-emptiness alone is insufficient); `test_changelog_facts.py` green. **(S, low.)**

---

## Phase 2 — Split the metadata snapshots into `kayak-data` (the strategic foundation)

> The biggest change; dissolves Pass-2's hardest findings. **Only machine-generated snapshots move** —
> `data/db/*.csv`, `reaches.json` (geom), `reaches-gradient.json` (gradient). **Hand-written stays in the code
> repo:** `data/db/migrations/*.sql`, `data/sources.yaml`, `data/discover/`. Runtime never reads the CSVs
> (Python reads the DB via `DATABASE_URL`, PHP via `SQLITE_PATH`); only setup/deploy/test/snapshot/deploy-apply
> paths do. **Consumption: a git submodule at `data/db/snapshots/`** (NOT `data/db/` — `migrations/` keeps that
> dir occupied; a submodule can't overlay tracked code). Pinned → reproducible CI + *reviewed* data promotion
> (prod only ever applies a pinned, CI-checked snapshot — which directly fixes #2's "rides in unreviewed").

- **R2.1 [L] — create `kayak-data`; move `data/db/*.csv` + the two JSONs.** Carry the relevant `.gitattributes`
  lines (the `-diff` collapse applies to **3** files — `huc_name.csv` + the two JSONs; `reach.csv` is
  deliberately diff-able). Specify the `kayak-data` access/branch model (who pushes; the bot's push to it). **(M, low.)**
- **R2.2 [L] — wire the code repo to consume the submodule. Bounded but NOT a one-line `DATA_DIR` repoint**
  (pass-3): add a `DB_SNAPSHOTS_DIR` constant in `kayak.config` (= `DATA_DIR/"db"/"snapshots"`), keep
  `DATA_DIR/"db"/"migrations"` as-is, and repoint **every** reader — the pass-3 inventory: `scripts/import_metadata.py`
  (`--in-dir` default `:248`), `scripts/export_metadata.py` (`:141`), `tests/test_scripts/test_migration_csv_reconciliation.py`
  (`SOURCE_CSV` `:27`), `tests/test_committed_reach_geom.py` (`:36`), **`tests/test_cli/test_fetch_usgs_ogc.py:134`**
  and **`tests/test_schema_doc_sync.py:106`** (both read `source.csv`; the latter hardcodes the path independent
  of `DATA_DIR`), `scripts/deploy.sh`, `.github/workflows/ci.yml`, `deploy/SETUP.md`, the dev quick-start, and
  `CLAUDE.md`. **CI blocker (required, not optional):** the `test` job checkout (`ci.yml:257`) has no
  `submodules:` key (defaults false) — add `submodules: true` (+ a token if `kayak-data` is private), else the
  five snapshot-reading tests `FileNotFoundError`. **(L, med.)**
- **R2.3 [M] — repoint the bot AND the human JSON-commit workflow.** Two writers move, not one (pass-3):
  `snapshot_metadata.sh` (the CSV bot — its `data/db/*.csv` pathspecs `:66,71,73`, the `OUTSIDE_STAGED` guard
  `:40`, and the on-`main` guard `:33-37` move to `kayak-data`'s layout; note the on-`main` guard's *rationale*
  changes — `kayak-data` is not an editable-install tree, so it's a plain push-to-right-branch guard, not a
  "don't deploy a feature branch" guard) **and** the dev's manual `export_metadata.py` → **commit the JSONs**
  step (`CLAUDE.md:207`, `SETUP.md:114`) now commits to `kayak-data`. **(M, med.)**
- **R2.4 [M, the deploy-apply fix — pass-3 critical] — rewrite `deploy.sh`'s geom/gradient apply for the
  submodule.** Today `deploy.sh:122` gates the apply on `git diff $old_sha $new_sha -- data/db/reaches.json`
  in the *code* repo; post-split that path is a submodule gitlink, so the diff matches nothing → **the apply
  never fires → prod geom silently goes stale** (reintroducing the round-2 `f8b475e` latent bug). And
  `import_metadata.py --geom-only` reads `data/db` (`:248,266`), now empty. **Fix:** detect a submodule-pointer
  change (or always-apply on a pin bump) and diff the old/new submodule SHAs *inside* `data/db/snapshots`; and
  repoint `import_metadata.py`'s `--in-dir`/default to `DB_SNAPSHOTS_DIR`. **(M, med.)**
- **R2.5 [S — pass-3] — name the submodule-pin-bump mechanism + the fresh-clone bootstrapping guard.** Nothing
  bumps the code-repo submodule pointer today; without a mechanism the pin is **manual forever** and R4.1's
  "when the pin is bumped" is not automatic. Specify it: a scheduled GH Action (or a documented manual step)
  that opens a *reviewed* pin-bump PR after the bot snapshots — this is the reviewed-promotion gate, so manual+
  reviewed is acceptable; state which. And name the **fresh-clone hazard**: a `git clone` without
  `--recurse-submodules` makes `test_committed_reach_geom.py:74` / `test_migration_csv_reconciliation.py:91`
  error (not skip); mitigate with a skip-if-snapshots-absent guard in those tests + a "run `git submodule
  update --init`" line in the quick-start. **(S, low.)**
  **Verify (Phase 2):** a from-scratch `init-db`+`import_metadata` from the submodule reproduces prod
  byte-for-byte (round-5 reconciliation, now cross-repo); CI (with `submodules: true`) runs the reconciliation +
  committed-geom tests green; a deploy with a changed pinned geom **applies** it (R2.4); the bot's nightly run
  pushes to `kayak-data`, and the code repo's `main` `git log` shows **zero** snapshot commits.

---

## Phase 3 — Code-repo branch protection (the #1 lever — now clean)

- **R3.1 [S config] — admin-enforced branch protection on the code repo `main`** (require a passing-CI PR;
  "include administrators" ON). **No bot exception needed** — the snapshot bot is in `kayak-data`. The lever
  Pass-2 proved unbuildable *in-place* (owner-identity bot), now trivial post-split: `9b428bb`-style human
  direct-to-`main` is rejected, no GitHub App required. Document the required checks in `deploy/SETUP.md`.
  **Verify:** a direct `git push origin main` on the code repo is rejected; merges require green CI; the nightly
  snapshot still lands (in `kayak-data`). **(S, low.)**
- **R3.2 [S] — add the reconciliation test file to the mypy CI scope + pre-commit** (the `set[str] = {}` class
  that broke CI; mypy flags it but CI's scope excluded `tests/`). Scope to exactly
  `tests/test_scripts/test_migration_csv_reconciliation.py` (`mypy tests/` floods 841 errors; the one file is
  clean + carries typed helpers worth checking). Belt-and-suspenders now that every code change is a CI-gated
  PR. **Verify:** `set[str] = {}` fails mypy in CI + pre-commit; the file's annotations pass. **(S, low.)**
- **R3.3 [defer, explicit] — commit-message convention.** The #1 finding's "misleading subject" half. Lower
  value post-protection (every change is a PR whose title + review catch a misleading subject); a commitlint
  hook adds friction for a single maintainer. Deferred, recorded so it is not a silent gap.

---

## Phase 4 — Cross-repo reconciliation (the #1 mechanism, simplified by the split)

> The split *dissolves* Pass-2's R3.2 defects (the `.csv` glob couldn't stage a `.txt`; no step read `source.csv`
> back). The bot is out of the reviewed repo; reconciliation is a code-repo CI check against the *pinned* snapshot;
> every `PENDING_RECONCILIATION` edit is a CI-gated code-repo PR — so the `{}` footgun can't reach `main`
> unreviewed regardless of substrate. **Pass-3-confirmed: this genuinely closes the #1 incident**, it doesn't
> just relocate bookkeeping (under R3.1 the stale-allowlist test catches a forgotten PENDING-clear pre-merge).

- **R4.1 [M] — reconciliation as a cross-repo CI check.** `test_migration_csv_reconciliation.py` reads the code
  repo's `migrations/*.sql` (`MIGRATIONS_DIR`, stays) against the *pinned-submodule* `source.csv`
  (`SOURCE_CSV` → `DB_SNAPSHOTS_DIR/"source.csv"`, the repoint R2.2 makes — name it here since the lag logic
  depends on reading the *pinned* CSV). The lag — a just-merged migration whose source isn't in the pinned
  snapshot yet — is bridged by `PENDING_RECONCILIATION` exactly as today (the migration PR adds the name; the
  later reviewed pin-bump PR clears it once the bot has snapshotted prod), **but** every PENDING edit is now a
  reviewed PR. **Verify:** a migration wiring a new source passes via PENDING before the pin catches up;
  bumping the pin + clearing PENDING passes CI; reverting either fails (caught pre-merge by R3.1). **(M, low.)**

---

## Phase 5 — Residual LOW

- **R5.1 [LOW] — #98 gradient hover-dot has no behavioral test.** A Playwright case (geom-bearing reach) hovering
  the profile and asserting the `L.circleMarker` dot at the hovered river-mile. **Verify:** fails if the #98
  handler is stubbed. **(M, low.)**

---

## Deferred (documented decisions, not gaps)

- **`PENDING_RECONCILIATION` stale-allowlist test is dormant (review §3 LOW)** — by design: with the set empty
  the test passes trivially; the live reconciliation tests do the real work. Informational (the review itself
  rates it "Correct" as-is); no action, recorded so the next author knows the guard value is zero until the
  allowlist is non-empty. (Captured here so the v4 reorg doesn't drop it — pass-3 F1.)
- **Bot self-gate / `check-snapshot-consistency.py` (prior R4.2)** — *demoted to optional* (pass-3): once the
  split + pinned-submodule land, a bad snapshot can't reach the code repo's `main` except via a reviewed
  pin-bump PR whose CI already runs the reconciliation + committed-geom guards. A prod-side pre-push check in
  `kayak-data` only buys data-repo-history hygiene, which the pin-bump CI does not need. Build it only if a bad
  snapshot polluting `kayak-data` history becomes a nuisance; if built, it must be stdlib-only (prod has no
  pytest) and commit any override list it checks *into* `kayak-data` (the data-repo bot can't read a code-repo
  list).
- **General snapshot-column drift guard (review §1/§2 prescription)** — the review wanted a guard catching a
  non-exempt column drift from *any* source. R1.2 fill-only closes the `seed_gauge_display` origin; the
  pinned-submodule reviewed-promotion (R2.5) catches *any* drift at pin-bump CI. So a separate column-guard is
  lower-value than the review estimated (it predates the split decision) — deferred; revisit if a non-seed
  drift source appears.
- **`deploy.sh` pipeline-timer race (review §3 LOW)** — self-healing (#93 is an ancestor of #94), not the 1000×
  bug; revisit if orphan emails annoy.
- **Commit-message lint** — see R3.3.
- **Config-clone instead of submodule (R2.2 alternative)** — rejected: `kayak.config` has no path-env override
  today, so the clone is *not* lighter (it needs the same new constant), and it loses the reviewed-promotion
  property. Submodule chosen.

## Sequencing rationale

**Phase 1 first** — the literal round-6 debt (gauge 217, CHANGELOG, seed_gauge_display safety), split-independent,
shippable as one tiny PR today, and must merge before this plan is archived. **Phase 2 (the split) is the
strategic foundation** the rest depends on; largest change, carries the rebuild-reproduces-prod + deploy-applies
gates. **Phase 3 (branch protection) only after Phase 2** (else it locks out the in-repo bot — the Pass-2
contradiction). **Phase 4** is the reconciliation the split simplifies. Phase 5 mops up. Mind the
migration-number-collision note; run the full local gate before pushing. Total: ~12 R-items across ~5–6 PRs;
Phase 2 is the bulk.

---

## Version log

- **v1** — initial draft from the round-6 review.
- **v2** — maintainer red-team: unified #1+#2 under one root cause (un-CI-gated direct pushes to `main`).
- **v3** — cold red-team pass-1: redesigned #2 from unsound parse-migration-pins to an override approach;
  corrected Phase 3 for prod reality (pytest `[dev]`-only; `grep -vxF` no-op; `mypy tests/` floods; branch
  protection vs the bot).
- **v4** — cold red-team pass-2 + **maintainer decision: split snapshots into a data repo.** Pass-2 found the
  registry incomplete (~18 overrides) and the bot pushes *as the repo owner on a personal repo* (no in-place
  branch-protection distinguishes bot from human → would need a GitHub App). The split dissolves both (clean
  admin-protection; fill-only protects every value uniformly) and the Pass-2 `.csv`-glob/auto-clear defects.
- **v5** — cold red-team pass-3. **R1.1 corrected (real bug):** `init-db` *stamps* migrations, so the migration
  alone leaves a from-scratch rebuild reproducing the clobbered CSV — R1.1 now hand-edits `gauge.csv` too and
  the Verify is fixed. **R1.2 sharpened:** the apply SELECT lacks the current display columns (fill-only needs
  the read); per-column NULL-keying specified; `--force`-recomputes-all re-clobbers all overrides, so added a
  `--gauge <name>` targeted-refresh path. **Phase 2 completed:** added the two missed `source.csv` readers
  (`test_fetch_usgs_ogc`, `test_schema_doc_sync`), the `DB_SNAPSHOTS_DIR` constant (DATA_DIR can't be blanket-
  repointed — `migrations/` and snapshots diverge), the **required** `ci.yml submodules: true`, the
  **deploy.sh geom-apply rewrite** (the submodule breaks change-detection → prod geom would go stale — R2.4),
  the human JSON-commit-workflow redirect + the on-`main` guard semantics note (R2.3), and the pin-bump
  mechanism + fresh-clone bootstrapping guard (R2.5). **R4.2 demoted to optional** (the pinned-submodule CI
  already gates promotion — over-build the split made redundant). **Added** the dormant-test LOW + the general
  column-guard to Deferred with rationale. *Pass-3 confirmed sound: Phase 1 is independently shippable today;
  R4.1 genuinely closes #1 under R3.1; all grep Verifies parse + go true; v4's R1.1 path change (grep the
  migration file, in the code repo) was itself a correct fix the split required.*
