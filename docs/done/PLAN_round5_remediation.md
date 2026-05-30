> **Archived 2026-05-30 — as-built record of the round-5 remediation.** Every R-item shipped, each with a
> committed guard: R1.1/R1.2/R1.5 **#85** · R1.3 **#86** · R2.1 **#87** · R3.1–R3.4 **#88** · R4.2–R4.5
> **#89** · R4.1 **#90** (plus an R4.5 ci-skip-gap follow-up, #91). The R2.1 guard
> (`tests/test_remediation_claims.py`) now enforces this plan's own grep-checkable Verifies. Companion:
> [`REVIEW_round5_2026-05-29.md`](REVIEW_round5_2026-05-29.md), [`IMPL_round5.md`](IMPL_round5.md).

# Round-5 Remediation Plan

**Source:** [`REVIEW_round5_2026-05-29.md`](REVIEW_round5_2026-05-29.md) (graded **B−**, ▼ from B — a
verification-integrity downgrade). **Branch:** `review-5`.

> Remediates the round-5 review. Findings were cold-audited by 6 facet auditors, the HIGH + security
> items hand-re-verified against committed `HEAD`, and **this plan is iterated to convergence across
> red-team passes against source** (see the version log at the bottom). Pass 1 corrected the Phase-2
> guard design (`grep -c` exits 1 on a zero count — the post-fix success case — so count in Python),
> the R4.1 test target (the #79 handler is in `feature-map.js` on *detail* pages, not `/map.html`), and
> an R-ID collision (mktemp was R1.4, which already means the allowlist → R1.5).

**Standing thesis (rounds 1–5):** *durable guards that make a class of drift impossible beat one-off
corrections.* Round 5 proved the **contrapositive** from the prior round's own remediation: the three
Phase-1 fixes routed "⏳ out-of-band on prod" (R1.1, R1.3) or silently dropped (R1.2) **evaporated** —
none reached the repo — while **R1.4, the one Phase-1 item that shipped as a committed, tested PR,
held perfectly.** So this plan's rule is absolute: **every fix lands as a committed PR carrying a
committed guard, and we mechanize a *claim-vs-source* check (Phase 2) so no future review can record a
fix as done while the source disagrees.** "Applied out-of-band on prod" is banned for tracked files —
in this `git pull --ff-only` model where `deploy.sh:53` aborts on a dirty tree, a hand-edit is either
reverted by the next pull (re-arming the bug) or it blocks deploys.

**Execution:** one PR per phase (Phase 1 may split), review → merge → verify, all in the `review-5`
worktree (never the live tree). **Phase 1 must merge before Phase 2** — the Phase-2 guard parses the
*archived* round-4 plan's Verify lines, and R1.1's `grep -c … → 0` only passes once Phase 1 deletes the
line. Legend: effort **S/M/L**, risk low/med/high. **Verify** lines let prod cross-check each fix.

---

## Phase 1 — Land the three non-landings, each with a committed guard (the sharp edges)

> These re-use round-4's R1.1/R1.2/R1.3 numbers deliberately — they *are* those items, never landed.
> R1.4 is intentionally skipped here: in the round-4 lineage R1.4 = the review-apply allowlist (shipped
> #53, verified sound this round). The opportunistic same-file hardening below is numbered **R1.5** to
> keep R-IDs stable across plans (the property Phase 2 leans on).

- **R1.1 [HIGH, ops] — delete `scripts/db_push.sh:134` `DELETE FROM pages;`.** The table was dropped by
  `0006_drop_pages.sql` (58 migrations ago) and has no other consumer in the repo (grep of `src/ php/
  scripts/` finds only this dead line). **Mechanism (corrected pass 1):** the merge runs as
  `sqlite3 "$NEW_DB" <<SQL … SQL` *without* `-bail`, so `no such table: pages` prints an error but the
  script **continues and the `COMMIT` still runs** (the observations are correctly merged); `sqlite3`
  then exits non-zero, and *that* trips the remote heredoc's `set -euo pipefail`, aborting **after** the
  commit but **before** the swap (`:154–159`) and the timer-restart (`:176–179`) — stranding the 4 timers
  stopped at `:100–106` and leaving the (correctly-merged) `$NEW_DB` un-installed. **Fix:** delete line
  134. **Guard:** the Phase-2 Verify-runner (R1.1's own `grep -c … → 0`). **Verify:**
  `grep -c 'DELETE FROM pages' scripts/db_push.sh` → `0`. **(S, low.)**
- **R1.2 [HIGH, ops] — `db_push.sh` leaves the 4 timers stopped on any failure between stop (`:100–106`)
  and restart (`:176–179`).** Only the integrity-check branch (`:145–148`) restarts, and only on its own
  failure; *every other* step after the stop — the checkpoint/`.backup` (`:109–110`), `gunzip` (`:113`),
  the merge `sqlite3` (`:116`, i.e. the R1.1 abort site), and the swap/prune (`:154–173`) — has none.
  **Fix:** define a `restart_timers()` helper (a `for u in kayak-pipeline.timer kayak-decimate.timer
  kayak-backup-weekly.timer kayak-backup-hourly.timer; do sudo -n systemctl start "$u" || true; done`,
  with `local u`) inside the remote heredoc; immediately after the stop block install
  `trap restart_timers EXIT`; **replace** the manual restart at `:145–148` with a bare `exit 1` (the trap
  now covers it); call `restart_timers` for the success path then `trap - EXIT` (so a clean run restarts
  exactly once). Restart the **4 timers** only — the stop block also stops the 4 `.service` units, but
  they're `Type=oneshot` (timer-driven), so `systemctl start …service` would *run the job*, not re-arm a
  schedule; this matches the existing restart block (the asymmetry is intentional). Use `sudo -n
  systemctl` (the script runs as `pat`; all 3 existing systemctl calls use `sudo -n`). **Guard:** a
  grep-style `scripts/check-db-push-trap.sh` (the `check-phpstan-level.sh` / `verify-systemd-units.sh`
  pattern — *not* a new test framework; the repo has no `bats`) wired into `ci.yml`, asserting a
  `trap … EXIT` appears between the stop and restart blocks. **Verify:** the guard greps the trap; on a
  sandbox copy, injecting `false` after the stop block leaves the 4 timers `active` (manual — the dev Mac
  has no systemd). *(Note: the `trap` lives inside the `<<'REMOTE'` quoted heredoc, which shellcheck
  treats as opaque, so `ci.yml:90`'s `shellcheck --severity=warning` stays green — verified pass 1.)*
  **(S, low.)**
- **R1.3 [MED, security] — `php/includes/source_url.php:32` rejects `\r\n\0` but not TAB,** so
  `j⇥avascript:` / `da⇥ta:…` slip the scheme check (`:38`) and render as a clickable `href` in the
  maintainer review UI (`review_handler.php:269`); browsers strip the tab → live `javascript:`/`data:`
  (CSP-mitigated, so MED). **Fix:** `preg_match('/[\r\n\t\0]/', …)` (browsers strip exactly TAB/LF/CR per
  WHATWG → closes the live-exploitable class; `\f`/`\v` also defeat `parse_url` but browsers don't
  reconstitute them, so the resulting `href` is inert — correctly out of scope), **and** update the
  docstring `:20` substring `CR/LF/NUL` → `CR/LF/TAB/NUL`. **Guard:** two new cases in
  `test_dangerous_schemes_rejected()` (`SourceUrlTest.php:72–83`, the scheme-rejection test — not the
  CRLF/header-injection test at `:85–91`). **Verify:** `sanitize_source_url("j\tavascript:alert(1)")` and
  `("da\tta:text/html,x")` return `''` (both currently return non-empty — confirmed pass 1); the suite
  goes green. **(S, low.)**
- **R1.5 [LOW, ops/hardening — opportunistic, no review finding] — while `db_push.sh` is open, `mktemp`
  the predictable `/tmp` paths.** `:96–97` use `/tmp/kayak-{live-final,new}-${TS}.db` and write via
  `gunzip -c … > "$NEW_DB"` (`:113`, `>` follows a pre-planted symlink); this runs over interactive `ssh`,
  *not* under the units' `PrivateTmp`. Single-tenant Hetzner makes it LOW. **Fix:** replace each path with
  a bare `$(mktemp)` (→ `$TMPDIR`/`/tmp`, preserving the current location — *not* the
  `kayak-install-runtime-config.sh:29` next-to-dest template, which would be a behavior change). `.backup`
  onto, and `gunzip > `, a pre-existing mktemp file both work (verified pass 1). **Verify:** the two paths
  come from `mktemp`; the script still round-trips a push on a sandbox. **(S, low — fold into the R1.2 PR;
  drop if it complicates review. Not in the review; flagged by the ops facet as a same-file LOW.)**

---

## Phase 2 — Mechanize the claim-vs-source guard (the round-5 lever)

- **R2.1 [the lever] — add `tests/test_remediation_claims.py`: every *mechanically-checkable* `Verify`
  line in an archived `docs/done/PLAN_round*_remediation.md` must pass against `HEAD`.** Design (hardened
  by pass 1):
  - **Glob `docs/done/PLAN_round*_remediation.md` ONLY** — never `docs/` root. The in-flight round-5 plan
    itself contains `DELETE FROM pages` multiple times, so an over-broad glob would parse its own un-landed Verify and
    stay RED (the exact self-reference trap round-4 R2.1 hit and fixed with a `project-review-*/` exclude).
  - Parse each `**Verify:**` **body** field (not the header `Rx.y #NN` shipped-ID list — that has no
    commands; this deliberately diverges from the review §0's header-based phrasing, which is imprecise).
    Extract the runnable subset: backticked `` `grep -c '<pattern>' <path>` `` followed by `→`/`->` and an
    optional-backticked integer N. Strip the backticks around N; handle the Unicode `→` (U+2192).
  - **Count in pure Python — do NOT shell out to `grep`.** `grep -c` returns **exit code 1 when the count
    is 0** (the *post-Phase-1 success* case for R1.1), so a `subprocess.run(check=True)` would crash
    exactly when the guard should pass; and the ambient `grep` flavor varies across hosts/PATH (GNU grep,
    BSD grep, and `ugrep` differ on `-E`/exit semantics). Implement as `sum(1 for ln in (repo_root/path).read_text().splitlines()
    if pattern in ln)` and assert it equals N. Note this is a substring/line count equivalent to
    **`grep -Fc`**, *not* bare `grep -c` (which is basic-regex — e.g. `grep -c 'FROM.pages'` → 1 where the
    substring → 0). So **the grammar accepts literal patterns only**: a parsed pattern containing a BRE
    metacharacter (`. * [ ] ^ $ \`) is treated as un-parseable (counter (b) below), so the substring count
    can never silently diverge from the documented command. Both the exit-code and grep-flavor hazards
    vanish, and today's sole in-scope pattern (`DELETE FROM pages`) is metacharacter-free.
  - **Non-vacuity, two assertions:** (a) found ≥1 runnable Verify, and R1.1's `DELETE FROM pages` → 0 is
    among them and passes; (b) an **"unparsed command-attempt" counter is 0** — flag any backticked span
    in a `**Verify:**` field that **begins `grep -c` _and contains a quote_ (`'`/`"`)** — i.e. a genuine
    command attempt — that the strict grammar did *not* fully consume (double-quoted pattern, `->` arrow,
    apostrophe-in-pattern, quoted path, or a BRE metacharacter in the pattern), and `assert` it's zero.
    **Requiring the quote is load-bearing — it is the third self-reference fix this round:** a bare prose
    mention of the token must NOT count, or once *this* plan is archived to
    `docs/done/PLAN_round5_remediation.md` (it matches the glob) the guard would self-trip on R2.1's own
    **Verify** below ("a malformed `grep -c` line"). That is the post-archival mirror of the in-flight-glob
    trap (round-4 level-8 → `project-review-*/`; round-5 in-flight → `docs/done/`-only glob; this → the
    quote-scoped counter). Without (b), a future Verify written in an unparsed *command* form would silently
    drop while R1.1 keeps the suite green — the "claim recorded but unchecked" hole this guard closes.
  - **Scope rationale (state it):** restrict to `grep -c … → N`. Broadening to `grep -rin … → none` would
    land the guard **RED forever** — round-3 R2.3's `grep -rin "level 8" … → none` fails at HEAD on
    `phpstan.neon:10`'s intentionally-permanent "level 8->9" historical line (verified pass 1). The prose
    remainder (26 of the 27 shipped review-IDs have prose Verifies — "the test runs in CI", "maps render")
    is covered by Phase 1's "each fix ships a committed test" rule, not by this guard.
  - Add a one-line **Verify-grammar note to the plan template** so future authors stay inside the
    parseable form (backticked `` `grep -c '…' <path>` `` → backticked N).

  **Lands green only after Phase 1** deletes `db_push.sh:134` (today R1.1 = 1 ≠ 0 → correctly RED).
  **Current mechanical coverage = exactly 1 line (R1.1)** — verified the only runnable `grep -c` Verify
  across both archived plans — but it would have caught *the* round-4 non-landing the day round 4 was
  archived, and every future grep-style claim is now enforced. **Verify:** passes post-Phase-1; reverting
  the R1.1 deletion turns it red; a Verify line with a deliberately-wrong `→ N`, or a malformed
  `` `grep -c `` line, turns it red. **(M, low.)**

---

## Phase 3 — Data audit-trail + doc drift

- **R3.1 [MED, audit-trail] — reach 118's HUC changed on prod + in `reach.csv` with no migration.**
  `8ce7366` carried `aw_10976` HUC `180102060303 → 180102060502`; no migration wires it, and `reach.huc`
  isn't a documented migration-exempt column (CLAUDE.md:207 exempts only `geom`/`gradient_profile`).
  **Decision: document, don't migrate** (red-team-confirmed). `reach.huc` *is* a column in `reach.csv`
  (not excluded like geom), but it's populated by `levels assign-huc`, which is a deterministic
  point-in-polygon over the WBD HUC12 layer (`kayak.huc.assign::assign_one`) — morally the same
  *tool-derived, snapshot-carried* class as geom/gradient (it stays *in* `reach.csv` only because a single
  HUC code diffs cleanly, unlike the multi-MB geom blob split to `reaches.json`). The audit-trail rule
  ("per-row reach backfills go via migration") is about **hand** edits; an `assign-huc` run isn't one.
  Verified: among pre-existing reaches, **only** reach 118's HUC changed in this snapshot (the reaches
  13/14 sort_name changes in the same batch *are* migration-backed — `0068_wire_crooked_basin.sql:139–140`).
  **Fix:** add `reach.huc` to CLAUDE.md's "documented exceptions" note as assign-huc-derived /
  snapshot-carried, and widen the `feedback_migration_over_db_push` scope note to "hand edits." **Verify:**
  CLAUDE.md names `reach.huc` as tool-derived next to geom/gradient; the data is unchanged (a fresh
  `init-db`+import reproduces `180102060502` from the CSV). *(Rejected: a one-row `UPDATE` migration —
  pure noise if assign-huc is the real mechanism.)* **(S, low.)**
- **R3.2 [MED, drift] — `fetch-usgs-ogc` is documented gauge-keyed; #75 made it source-keyed.** Sweep the
  reference-doc + in-code refs (the `feedback_sweep_refs_on_change` lesson): `CLAUDE.md:82`, `CLAUDE.md:171`,
  `README.md:90`, **and the in-code drift** `src/kayak/cli/fetch_usgs_ogc.py:3` header docstring ("all
  gauges with a usgs_id") + the `:75` `--site` help — all contradict the file's own authoritative
  `_build_site_map` docstring (`:86–88`, "selection no longer keys on `gauge.usgs_id`"). **Fix:** reword
  each to "for gauges **linked to a USGS source**." **Scope note:** the sweep deliberately covers
  maintainer-facing reference docs (CLAUDE.md/README) + the source tree, **not** historical plan rationale
  — `docs/PLAN_add_gauges_reaches.md`'s "set `gauge.usgs_id`" is correct *add-a-gauge* guidance, and
  `docs/PLAN_montana_gauges.md:84`'s "auto-discovered via `gauge.usgs_id`" is a point-in-time scope note
  in an in-progress plan; both are left as-is by design. **Verify:**
  `grep -rn "gauges with .*usgs_id\|all gauges with a usgs_id" CLAUDE.md README.md src/` returns nothing.
  **(S, low.)**
- **R3.3 [LOW, drift — curation-caveated] — `CHANGELOG.md` `[Unreleased]` is empty; #75 + Batch A/B/C
  unrecorded.** The file's policy is "curated and thematic," and the R2.2 guard deliberately checks facts
  not completeness — so this is curation lag, not a false fact, and trips no test. **Fix:** add a thematic
  `[Unreleased]` entry for the source-based USGS-OGC fetch refactor + the Batch A/B/C gauge/reach additions
  — **prose only; do not pair a shipped review-ID (`R<n>.<n>` + `#<pr>`) with an open-status word**
  (tracked/residual/still/open/…), the one thing `test_changelog_facts.py` forbids. **Verify:** the section
  is non-empty; `test_changelog_facts.py` stays green. **(S, low.)**
- **R3.4 [LOW, integrity] — correct the round-4 archive's false "shipped" record.**
  `docs/done/PLAN_round4_remediation.md:3–7,29` states "Every finding shipped across PRs #53–#69" and "the
  R1.1/R1.3 prod-host items were applied out-of-band (⏳)" — but R1.1/R1.2/R1.3 never reached the repo and
  ship in *this* round. For a verification-integrity remediation, leaving the archived record reading
  "shipped" is the exact trust gap the round is about (surfaced by the PR-84 live review). **Fix:** add a
  clearly-marked top-of-file **erratum** (append-only — preserve the historical body, don't rewrite it):
  e.g. "**Erratum (round 5, #&lt;this PR&gt;):** R1.1/R1.2/R1.3 were recorded shipped/⏳ here but never
  reached the repo; they actually land in round 5 — see `project-review-5/`." **Verify:** the round-4
  archive carries the erratum; the Phase-2 guard (once it exists) still passes against the round-4 archive
  — R1.1's `grep -c … → 0` Verify holds after Phase 1, and the erratum adds no `**Verify:**` field or
  quoted `grep -c` span. **(S, low.)**

---

## Phase 4 — CI / test completeness (new-work coverage)

- **R4.1 [MED, coverage] — the #79 right-click map popup/Copy has no behavioral test.** *(Refines the
  review, which juxtaposed this with `/map.html`'s `smoke.spec.ts:85` — but `/map.html` loads
  `static/map.js`, which has **no** `contextmenu` handler. The #79 handler is in `static/feature-map.js`
  (`L.DomEvent.on(map.getContainer(),'contextmenu',…)` `:503`; `.latlon-popup` `:538`;
  `clipboard.writeText` `:523`), which is loaded only by **detail** pages — `reach_detail.php:639`,
  `gauge_detail.php:847`, `description_detail.php:85`. A `/map.html` test would be vacuous.)* **Fix:** a
  Playwright case (`tests/js` is Playwright) against a **reach/gauge/description detail page** that fires
  `contextmenu` on `.leaflet-container` and asserts a `.latlon-popup` renders a coordinate string (and the
  Copy button exists). **Scaffolding (pass-2 — load-bearing):** `tests/js` has **no detail-page route
  today**, and the JS test DB is `levels init-db`-only (zero reaches), so the case must **seed a reach with
  `latitude_start`/`longitude_start`** (extend `editor.spec.ts:72`'s `seedReach`) — `reach_detail.php`'s
  `_render_reach_map` returns `[false, '']` and emits **no** `#reach-map` div / no `feature-map.js` for a
  coordinate-less, geom-less reach (`reach_detail.php:608–610`), so without a coordinate there's no
  `.leaflet-container` to fire on. **Verify:** the new case fails if the `feature-map.js` handler is stubbed
  out. **(M, low.)**
- **R4.2 [LOW, coverage] — committed reach geometry isn't validated at merge.** `check_reaches.scan_for_issues`
  (`check_reaches.py:246`) runs over synthetic reaches in tests and over the real 420 only in the prod
  pipeline soft-fail. **Fix:** a test that imports the committed `reach.csv` + `reaches.json` into in-memory
  SQLite and asserts `scan_for_issues()` is empty (guards the dev-only-regenerable geom at merge).
  **Verify:** passes on the current snapshot; a hand-corrupted geom endpoint fails it. **(M, low.)**
- **R4.3 [LOW, reuse] — `Makefile` `.PHONY` omits `test-php`** (the `check:` dep added by #81) plus
  `init-db`/`install`/`help`. **Fix:** add them. **Verify:** `make check` still runs PHP tests even if a
  file named `test-php` exists. **(S, low.)**
- **R4.4 [LOW, coverage] — `tests/test_scripts/test_migration_csv_reconciliation.py:38` only matches the `INSERT … SELECT`
  wiring form;** a future `INSERT INTO source (…) VALUES ('NAME', …)` extracts zero names and bypasses the
  guard (all 32 current wiring INSERTs use SELECT, so this is forward-looking). **Fix:** broaden
  `_SOURCE_INSERT` to also capture `VALUES ('<name>'`, **or** add an assertion that no `INSERT INTO source`
  in any migration uses `VALUES`. **Verify:** a synthetic `VALUES`-form INSERT missing from the CSV is
  caught. **(S, low.)**
- **R4.5 [LOW, coverage] — `ConfigTest::testEmitConfigJsonRoundTripsViaConfig` still *skips* (not fails)
  when `levels` is absent** (`:158`); it runs in CI only because the lint-misc job's `pip install -e .`
  (`ci.yml:142`) happens to put `levels` on PATH before PHPUnit (`:145`) — an implicit ordering dependency,
  and `KAYAK_LEVELS_BIN` is set nowhere in `.github/`. (Round-4 R4.1 already added `KAYAK_LEVELS_BIN`
  override support.) **Fix:** set `KAYAK_LEVELS_BIN` in the lint-misc job and make the test **fail** (not
  skip) when that env var is set but the binary is missing. **Verify:** the test is reported run (not
  skipped) in CI; unsetting the binary with the env set turns it red. **(S, low.)**

---

## Deferred (documented decisions, not gaps)

- **R7.1 OSMB dedup** — still deferred by the rule-of-three; revisit on a 3rd map consumer.
- **`db_push.sh:167` `mapfile`** — sibling `db_pull.sh` was de-`mapfile`d by #82 (dev-Mac bash 3.2), but
  this `mapfile` is inside the **remote** `<<'REMOTE'` heredoc (`:88–194`, run via `ssh … bash -s` on prod
  Debian bash 5), so it's safe; at most a one-line "remote side, bash 5" comment for parity. Not scheduled.
- The round-4 "Verified SOUND" set (R1.4 allowlist, all Phase-2/5 guards, agency/gradient chains,
  auth/CSRF/CSP/SQL core, the new Batch A/B/C data) needs no action.

## Sequencing rationale

**Phase 1 retires the three non-landings** (two HIGHs + the security MED) — the actual round-5 debt —
each as a committed PR with a committed guard, ending the "out-of-band on prod" pattern. **Phase 2 is
the point of the round:** it mechanizes the claim-vs-source check so this class can't recur, and it can
only go green *after* Phase 1, so it must merge second. Phase 3 clears the data-audit-trail + the two
prose drifts; Phase 4 closes the new-work coverage gaps. Mind the round-4 CI-config contention note if
any Phase-4 PR touches `ci.yml` concurrently. Total: ~13 R-items across ~4–5 PRs.

---

## Version log

- **v1** — initial draft from the round-5 review, pre-red-team.
- **v2** — pass-1 convergence (3 cold lenses vs. source). **Phase 2 (R2.1) rewritten:** count in Python
  (not `subprocess grep` — `grep -c` exits 1 on a zero count, the post-fix success case; + dev-box `ugrep`
  vs CI GNU-grep variance); added the unparsed-`grep -c` non-vacuity counter; pinned the glob to
  `docs/done/` only; noted it parses body `**Verify:**` fields (diverging from the review's header-based
  phrasing). **R4.1 target corrected:** detail page emitting `feature-map.js`, not `/map.html`/`map.js`
  (would be vacuous). **mktemp renumbered R1.4→R1.5** (R1.4 = the allowlist). **R1.1 mechanism** reworded
  (sqlite3 commits then exits 1 → parent `set -e`). **R1.2 guard** = grep-style `check-*.sh` not `bats`;
  `restart_timers()` helper form. **R1.3** exact docstring substring + test-method placement. **R3.2**
  scope note (excludes historical plan rationale incl. `PLAN_montana_gauges.md:84`). **R3.3** the no-open-ID
  constraint. *Lens A returned CONVERGED; B and C returned the changes above.*
- **v3** — pass-2 convergence. **R2.1:** the Python `pattern in line` count is `grep -Fc` (fixed-string),
  not bare `grep -c` (BRE) — so the grammar is restricted to **literal patterns** and counter (b) also
  flags a BRE metacharacter in the pattern (today's `DELETE FROM pages` is metacharacter-free, so no
  effect now). **R4.1:** added the load-bearing scaffolding fact — no detail-page route exists in
  `tests/js` and a coordinate-less reach renders no map container, so the test must seed a reach *with*
  `latitude_start`/`longitude_start`. Plus two cosmetic path/count fixes (R4.4 dir prefix; the in-flight
  self-reference count). *Pass 2's two substantive items were both forward-looking clarity, no correctness
  blocker.*
- **v4** — PR-84 live-review pass (external, reproduced against prod). **R2.1 counter (b) scoped to genuine
  command attempts** (a backticked `grep -c` span *with a quote*), so a bare prose mention of the token no
  longer self-trips the guard once *this* plan is archived to `docs/done/` — the post-archival mirror of
  the in-flight-glob trap, and the third self-reference fix this round. **Added R3.4** (erratum on the
  round-4 archive's false "Every finding shipped"). **R2.1 grep-flavor wording** made host-agnostic. *The
  live review reproduced every falsifiable claim against prod and rated the PR merge-ready; these are the
  two items it surfaced (§4 + §5).*
