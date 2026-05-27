# Round-4 Remediation Plan

> **Archived 2026-05-26 — as-built record of the round-4 remediation.** Every
> finding shipped across PRs **#53–#69** — R1.4 #53 · R2.1 #55 · R2.2 #58 · R2.3 #59
> · R2.4/R3.4 #54 · R3.1–R3.3 #56 · R4.1 #60 · R4.2+R5.3 #67 · R4.3 #62 · R4.4 #68 ·
> R4.5 #61 · R4.6+R4.7 #66 · R5.1 #57 · R5.2+R5.5 #63 · R5.4 #64 · R5.6 #65 · R5.7 #69
> — the R1.1/R1.3 prod-host items were applied out-of-band (⏳ below); version bump #70.
> Companion review: [`REVIEW_round4_2026-05-26.md`](REVIEW_round4_2026-05-26.md).

**Source:** `project-review-4/REVIEW.md` (graded **B**, ▲ from B−). **Branch:** `review-4`.

> Remediates [`REVIEW_round4_2026-05-26.md`](REVIEW_round4_2026-05-26.md) (graded **B**, ▲ from B−). Findings were cold-audited
> by 6 facet auditors, hand-verified, independently re-verified on prod (5 refinements folded in),
> and **this plan was iterated to convergence across four red-team passes against source.** They
> corrected R1.4 (a fix that would have regressed — `edit.php`'s field set, not propose's, drops
> lat/lon edits), R2.1 (6 stale strings not 2, and a self-referential guard that couldn't land green
> until `project-review-*/` was excluded), R4.4 (a wholesale table-diff is infeasible — only
> wired-source reconciliation works), R4.2 (won't run green on a stock runner), and R4.6 (a
> misattributed ref); surfaced one missed finding (R4.7); and the **fourth pass returned CONVERGED**
> (no new findings). v3 reflects all of it.

**Standing thesis (rounds 1–4):** *durable guards that make a class of drift impossible beat one-off
corrections.* Round-4 proved it — the one mechanized guard (the schema-doc↔models CI test) held,
while every drift finding this round is on an **un-mechanized** surface. **So Phase 2 front-loads the
guards: each new anti-drift check ships in the same PR as the fix(es) that make it pass, so CI goes —
and stays — green.**

**Execution:** one PR per phase (Phase 2 splits to ~4), review → merge → verify on prod, as in round-3
(#34–#52). **R1.1 and R1.3 are already in flight on the prod system** (⏳). **Verify** lines let the
prod system cross-check each fix independently. Legend: effort **S/M/L**, risk low/med/high.

> **CI-config contention note:** R2.1–R2.4, R4.1, R4.2, R4.6, R4.7 all touch `ci.yml` and/or
> `.pre-commit-config.yaml`. Land them in a deliberate order and rebase each on the last to avoid
> sequential-merge conflicts; don't open all the Phase-2/4 PRs simultaneously.

---

## Phase 1 — Sharp edges: the latent HIGH + the security MEDs

- **R1.1 ⏳ [HIGH, ops] — `scripts/db_push.sh:134` runs `DELETE FROM pages`** (table dropped by `0006_drop_pages.sql`); under the remote heredoc's `set -e` it aborts the restore before the swap + timer-restart. **Fix:** delete line 134. **Verify:** `grep -c 'DELETE FROM pages' scripts/db_push.sh` → `0`. **(S, low — in flight on prod.)**
- **R1.2 [HIGH, ops] — the same script leaves prod timers stopped on *any* failure between stop (`:100–106`) and restart (`:175–179`).** **Fix:** inside the remote heredoc, immediately after the stop block, install `trap 'sudo -n systemctl start kayak-pipeline.timer kayak-decimate.timer kayak-backup-weekly.timer kayak-backup-hourly.timer' EXIT` and clear it (`trap - EXIT`) only after the successful restart. **Use `sudo -n systemctl`** — the script runs as `pat` and uses `sudo -n` for every systemctl call (`:105/:147/:178`); a bare `systemctl` would lack privilege. The stop block also stops the 4 `.service` units, but they're timer-driven oneshots, so restarting the 4 **timers** is sufficient (matches the existing restart block — note the asymmetry is intentional). **Verify:** inject a `false` mid-merge on a sandbox copy → the 4 timers are `active` afterward. **(S, low.)**
- **R1.3 ⏳ [MED, security] — `php/includes/source_url.php:32` rejects `\r\n\0` but not tab,** so `j⇥avascript:` slips the scheme check (`:38`) and renders as a clickable `href` (`review_handler.php:269`); browsers strip the tab on click (CSP-mitigated). **Fix:** `/[\r\n\t\0]/` (browsers strip exactly tab/LF/CR per WHATWG → closes the whole class). **Verify:** new `SourceUrlTest` cases for `j\tavascript:` and `da\tta:` return `''` (the suite has `\r\n\0` cases at `:88–90` but no tab case). **(S, low — regex in flight on prod; the test is this plan's part.)**
- **R1.4 [MED, security/defense-in-depth] — `php/includes/review_logic.php:123` interpolates the column name `$f`** from `payload_json` into `"$f = ?"` with no apply-time allowlist. **Correction from red-team:** there is **no shared allowlist constant to reuse**, and the two obvious sources differ — `php/edit.php:49–54` is a 17-field *direct-write* path (`change_request_id = NULL`) that **never reaches `review_logic`**, and its set has **no lat/lon**; the apply path only ever handles **propose-originated** payloads, whose keys are set in `propose_handler.php:111–118`: `description, features` (+ for full/maintainer `display_name, latitude_start, longitude_start, latitude_end, longitude_end`). **Fix:** extract *that* 7-field set into a shared constant consumed by **both** `propose_handler.php` and `review_logic.php` (prevents future drift), and intersect `$f` against it at apply time — skip + log any other key. **Do not** use `edit.php`'s set (would silently drop legitimate proposed lat/lon edits). **Verify:** a forged `payload_json` key (`id`, `geom`) is dropped, not written; a normal `latitude_start` proposal still applies. **(S, low.)**

---

## Phase 2 — Mechanize the anti-drift guards (each lands with the fix it flags) — *the lever*

- **R2.1 [MED, drift] — "level 8" survives in 6 places** (not 2): `README.md:7` badge + `.pre-commit-config.yaml:73` comment are **stale current-state claims** (neon is `level: 9`); `php/status.php:54`, `php/_internal/index.php:29`, `php/includes/config.php:20` pin "level 8" in **technical** comments for the `PDO::query()|false`/`mixed` narrowing (which applies at level 9 too); `phpstan.neon:10` ("the level 8->9 bump") is a **correct historical** note. **Fix + guard:** (a) fix the two stale claims → "level 9"; (b) reword the three PHP comments to drop the version-pin (e.g. "for PHPStan's `PDOStatement|false` narrowing"); (c) leave `phpstan.neon:10`; (d) add a guard — a grep for the **literal-space** `level 8` / `level%208` across `*.md *.yaml *.yml *.neon *.php`, excluding `docs/done/`, **`project-review-*/`** (the in-flight review+plan quote historical "level 8" by nature, and archive to `docs/done/` later — as round-3's `project-review-3/` did in #46), `phpstan-baseline.neon`, and the `phpstan.neon` "8->9" historical line. The literal-space form sidesteps `CHANGELOG.md:89`'s legitimate hyphenated "level-8" prose; if ever broadened to `level[ -]?8`, exclude `CHANGELOG:89` too. **Verify:** the guard finds zero after (a)–(c) under that ignore-set; reintroducing a "level 8" current-claim fails it. **(M, low.)** *(v1 listed 2 files + a vacuous md/yaml-only Verify; v2 found all 6 strings; v3 added the `project-review-*/` self-reference exclusion the 3rd pass caught — without it the guard can't land green while round-4 is unarchived.)*
- **R2.2 [MED, docs/trust] — `CHANGELOG.md` carries false facts:** `:114` calls R1.5 "still … tracked as R1.5" (#48 closed it); `:113` brags it "rejected `javascript:`/`data:`" (R1.3 shows incomplete); `:95` "634 entries" (baseline = 104 entries / 218 summed `count:`); `:102` "floor 5% → 55%" (now 58%, #52). The PR-number omission is the file's stated **curation policy**, not a defect. **Fix + guard:** correct the four facts; add a guard that no review-ID recorded **done** in `docs/done/PLAN_round*_*.md` (use the header-summary "R1.5 … #48" lines as the closed-ID source — *not* the body bullets, which read as to-fix) is described in CHANGELOG `[Unreleased]` as open/residual/tracked. **Verify:** guard passes post-fix; flipping R1.5 back to "tracked" fails it. **(M, low.)** *(Facts #2–#4 stay unmechanized — acknowledged; the guard targets the highest-trust one.)*
- **R2.3 [LOW/MED, hygiene] — a completed plan escaped `docs/done/`:** `docs/PLAN_gradient_single_source.md:45` says "Implemented" (#42) but sits in `docs/` root, unindexed; `docs/done/README.md:32–33` still says round-3 "#34–#45" with R1.5/R2.6/R5.4/R5.7/agency as "deferrals" (shipped #47–#52). **Fix + guard:** `git mv` the gradient plan to `docs/done/` + index it; correct the index to "#34–#52, all but OSMB-dedup shipped"; add a guard that any `docs/*PLAN_*.md` whose `## Status` says **Implemented / done** lives under `docs/done/` and is indexed — **with an explicit allowlist for `docs/PLAN_production_discipline.md`** (a landed plan deliberately kept in `docs/` as a live cross-ref, per `docs/done/README.md:35–37`). Use the narrow "Implemented/done" trigger, **not** "merged/landed" (which would false-positive `PLAN_montana_gauges.md` (in-progress) and `PLAN_production_discipline.md`). **Verify:** guard passes; a done-plan left in `docs/` root fails it; the two allowlisted/in-progress plans don't trip it. **(M, low.)**
- **R2.4 [LOW, test guard] — extend `tests/test_schema_doc_sync.py`** (currently name-only, one-directional). **Fix:** add (a) **reverse coverage** — a documented column with no ORM counterpart fails — **adding `schema_migrations` to `_IGNORE_TABLES` in the same PR** (it's documented at `database-schema.md:434` but is raw-DDL, not in `Base.metadata`, so reverse-coverage would otherwise fail on landing); and (b) an assertion that the `source.agency` Notes enum lists every distinct agency value, driven off **`data/db/source.csv`** (the authoritative 9: `Calculation, NOAA, NWRFC, NWS, PacifiCorp, USACE, USBR, USGS, WA DOE`) — *not* `canonical_agency`'s map (only 4 overrides). **Verify:** extended test passes after R3.4; dropping `PacifiCorp` from the doc, or adding a phantom doc column, fails it. **(M, low.)**

---

## Phase 3 — Clear the remaining doc-drift (now under Phase-2 guards)

- **R3.1 [MED, drift] — `CLAUDE.md:107` mypy "(CI scope)" lists 2 scripts; `ci.yml:209` + `Makefile` run 3** (+`refresh_reach_elevations.py`). **Fix:** add the third. **Verify:** the line == `ci.yml`'s typecheck invocation. **(S, low.)**
- **R3.2 [LOW, drift] — ruff rule list omits `C901`** in `CLAUDE.md:110` + `CONTRIBUTING.md:35` (`pyproject.toml:79` has it). **Fix:** add `C901` to both. **Verify:** both strings match `pyproject.toml`. **(S, low.)**
- **R3.3 [LOW, drift] — `.env.example:21` cites `SETUP.md:633`** (a venv line; the prod `OUTPUT_DIR` lives in the Environment-file section). **Fix:** replace the line-number with a **section anchor** for the production environment-file section (confirm the exact heading at edit time — the two red-teams disagreed §3 vs §4, which is itself why a number is the wrong reference). **Verify:** the reference resolves to where `OUTPUT_DIR=/home/pat/public_html` is set. **(S, low.)**
- **R3.4 [MED, drift] — `docs/database-schema.md:67` `source.agency` enum omits `PacifiCorp`.** **Fix:** add it (→ 9). **Verify:** R2.4's enum assertion passes. **(S, low — lands with R2.4.)**

---

## Phase 4 — CI coverage gaps

- **R4.1 [MED, CI] — `tests/php/ConfigTest.php:147` (`testEmitConfigJsonRoundTripsViaConfig`) silently skips in CI** (resolves `levels` only from `KAYAK_LEVELS_BIN`/hardcoded path; CI puts it on PATH but sets no env). **Fix:** add the `which levels` PATH fallback (as `FunctionalTestCase::resolveVenvCommand` has) or set `KAYAK_LEVELS_BIN` in the lint-misc job. **Verify:** the test runs (not skips) in CI. **(S, low.)**
- **R4.2 [MED, CI] — no CI step validates systemd units** (shellcheck covers `*.sh`, not `.service`/`.timer`). **Correction:** a bare `systemd-analyze verify` will **not** pass on a stock runner — 11 units have a hard `EnvironmentFile=/home/pat/.config/kayak/.env` and all use `/home/pat/...` `ExecStart`/`WorkingDirectory`, which don't exist there. **Fix:** add a step over **both `*.service` and `*.timer`** that runs `systemd-analyze verify` and greps the output for genuine syntax/directive errors while tolerating the expected "path/EnvironmentFile not found" warnings (or runs with those paths stubbed). **Verify:** a deliberately malformed `OnCalendar=`/`ExecStart=` is caught; the expected path warnings don't fail it. **(M, med.)** *(Also conditions R5.3's Verify.)*
- **R4.3 [MED, ops] — `conf/mime-extras.conf` is absent from the drift manifest and the install runbook** (serves `application/geo+json` for the map overlays). **Fix:** add `conf/mime-extras.conf<TAB>/etc/nginx/conf.d/mime-extras.conf` to `scripts/check-config-drift.sh`'s manifest **and** a matching `sudo cp` in `SETUP.md §6`. **Verify:** drift-check lists it; a fresh-install dry-run installs it. **(S, low.)**
- **R4.4 [MED, data/test] — no test guards the migrate↔CSV reconciliation** (the gap that left GPRO3 in the DB but not the CSVs after 0063). **Correction:** a wholesale "migrate-from-empty vs import" table diff is **infeasible** — migrations are DDL + *targeted* backfills, not the full catalog INSERT, so a migrate-only DB has near-empty metadata. **Fix:** the meaningful invariant is *wired-source reconciliation* — a test asserting that every `source`/`fetch_url`/`gauge_source` row a migration `INSERT`s (the 0027/0063 class) also exists in the committed `*.csv`. **Verify:** passes once the GPRO3 CSVs are reconciled (post nightly snapshot); a future wire-via-migration without a CSV export fails it. **(M, med — confirm against the existing synthetic `tests/test_scripts/test_metadata_roundtrip.py`.)**
- **R4.5 [LOW, CI] — `scripts/check-php-coverage.sh:30` primary regex is dead** (Clover emits `<project>`/`<metrics>` on separate lines); the gate works only via the `tail -1` fallback (`:34`). **Fix:** drop the dead branch; document that the gate depends on the rollup `<metrics>` being last. **Verify:** still reports 60.42%; dead branch gone. **(S, low.)**
- **R4.6 [LOW, CI] — `docs/one-offs/*.sh` (4 files) are outside shellcheck scope** (`ci.yml:90`). **Fix:** add `docs/one-offs/*.sh` to the shellcheck invocation. **Verify:** shellcheck runs over them green. **(S, low.)** *(v1 also told me to fix a "level 8 comment" in `check-php-helper-prefix.sh:73` — there is no such string there; dropped. The real "level 8" strings are R2.1's.)*
- **R4.7 [LOW, CI] — `check-php-helper-prefix.sh` runs only as a pre-commit hook** (`.pre-commit-config.yaml:93`), bypassable via `--no-verify`; no CI job runs it (REVIEW:125, missed by v1). **Fix:** either add it to a CI lint step, or document it as a deliberately advisory pre-commit-only convention. **Verify:** if promoted, a prefix violation fails CI. **(S, low — decide promote vs. document.)**

---

## Phase 5 — Correctness & latent hardening

- **R5.1 [LOW, correctness] — `src/kayak/db/cache.py:178`/`:199` `update_latest_gauge` lacks the `source_id` tiebreak** the bulk rebuild (`:252`/`:271`) has; divergent only for byte-identical timestamps on a multi-source gauge, and overwritten by the bulk step within a full pipeline run. **Fix:** add `Observation.source_id.desc()` to both `order_by` blocks; drop the docstring self-hedge (`:325–327`). **Verify:** add a tied-timestamp case to `test_bulk_matches_per_gauge_loop` (`tests/test_db/test_cache.py:158`); it passes without relying on SQLite ordering. **(S, low.)**
- **R5.2 [LOW, correctness] — `migrate.py discover_migrations` keys `version` on the 4-digit prefix;** two files sharing one → the second silently skipped (`apply_pending:120`). **Fix:** raise on a duplicate prefix in `discover_migrations`. **Verify:** a test with two `0099_*.sql` raises. **(S, low — the #49/#50 collision class.)**
- **R5.3 [LOW, ops] — root-run `kayak-config-drift.service` / `kayak-cert-renewal-test.service` omit `SystemCallFilter=`/`CapabilityBoundingSet=`** that every `User=pat` unit carries. **Fix:** add `SystemCallFilter=@system-service` to both; `CapabilityBoundingSet=CAP_DAC_READ_SEARCH` for config-drift (it reads root-only `/etc/sudoers.d`; leave certbot's caps). **Verify:** R4.2's check green + the units still run on prod/staging. **(S, med — ties to R4.2.)**
- **R5.4 [LOW, ops] — `init_db.py --drop` (`:186` `Base.metadata.drop_all`) doesn't drop `schema_migrations`** (raw-DDL), so `--drop` over a behind DB can leave "pending" migrations whose re-run errors. **Fix:** `--drop` also `DROP TABLE IF EXISTS schema_migrations` (then `_ensure_tracking_table` recreates it empty). **Verify:** `init-db --drop` over an older DB → fresh stamp set, `migrate` a no-op. **(S, low.)**
- **R5.5 [LOW, correctness] — `migrate._split_statements:192` splits on every `;`,** including inside string literals (no current migration affected). **Fix:** guard against `;` inside quotes, or document the constraint + add a discovery-time assertion. **Verify:** a test migration with `'a; b'` is handled or rejected clearly. **(S, low.)**
- **R5.6 [LOW, ops] — `db_push.sh:26`/`db_pull.sh:25` default `REMOTE_BACKUP_DIR` inside the live repo** (`~/kayak/backups`). **Fix:** default to a non-repo path (e.g. `/home/pat/var/db-sync`). **Verify:** a sync writes outside `~/kayak`. **(S, low.)**

---

## Deferred (documented decisions, not gaps)

- **R7.1 OSMB dedup** — still deferred by the rule-of-three; revisit on a 3rd map consumer.
- `cli/pipeline.py except SystemExit` (REVIEW §4) — no current bug (no step calls `sys.exit`); future-residual only. Not scheduled; the R5.2 mindset covers the class.
- The "Verified SOUND" areas (R1.5 wrapper, R2.6, R5.7, schema-doc guard, gradient chain, auth/CSRF/SQL/CSP core, agency normalization) need no action.

## Sequencing rationale

Phase 1 retires the one HIGH + the two security MEDs (smallest, highest-severity). **Phase 2 is the
point of the round** — it mechanizes the four drift classes so Phase 3's prose fixes can't silently
regress, landing each guard green by fixing its current violation in the same PR. Phase 3 follows
Phase 2 (R3.4 lands with R2.4). Phases 4–5 are independent. Mind the CI-config contention note above.
Total: ~25 R-items across ~7 PRs.
