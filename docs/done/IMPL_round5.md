> **Archived 2026-05-30 — the as-executed playbook.** All six fix PRs landed (#85–#90) + the R4.5 follow-up
> (#91). Companion: [`PLAN_round5_remediation.md`](PLAN_round5_remediation.md),
> [`REVIEW_round5_2026-05-29.md`](REVIEW_round5_2026-05-29.md).

# Round-5 Implementation Plan (execution playbook)

**Companion to** [`PLAN_round5_remediation.md`](PLAN_round5_remediation.md) (the *what/why*; graded review
in [`REVIEW_round5_2026-05-29.md`](REVIEW_round5_2026-05-29.md)). This is the *how*: the PR breakdown,
concrete edits/sketches, sequencing, and the per-PR gate. **Iterated to convergence — see the version log.**

## Execution model

- **Worktrees, never the live tree.** Each PR is its own branch off `origin/main` via
  `KAYAK_HOME=/Users/pat/tpw scripts/new-worktree.sh <branch>`; edit/commit/push/PR there;
  `git worktree remove` when merged. (The `review-5` branch holds only the planning docs — PR #84.)
- **Full gate before every push** (the `feedback_run_full_gate_before_push` lesson — esp.
  `ruff format --check`, not just `ruff check`). The per-PR "Gate" lines below list the *relevant* subset;
  `make check` runs the lot.
- **Accumulate commits locally; push at a checkpoint** (avoids CI-run spam). Commit-msg style:
  lowercase `<type>: imperative`, body, `Co-Authored-By` trailer.
- **One logical change per PR**, reviewed → merged → `git pull` on the live `main` to deploy.

## PR map

| PR | Phase | R-items | Files | Guard/test shipped | Depends on |
|----|-------|---------|-------|--------------------|-----------|
| **1** | 1a ops | R1.1, R1.2, R1.5 | `scripts/db_push.sh`, `scripts/check-db-push-trap.sh` (new), `.github/workflows/ci.yml` | `check-db-push-trap.sh` in CI | — |
| **2** | 1b sec | R1.3 | `php/includes/source_url.php`, `tests/php/SourceUrlTest.php` | tab cases in `SourceUrlTest` | — |
| **3** | 2 lever | R2.1 | `tests/test_remediation_claims.py` (new) | the guard *is* the test | **PR 1** |
| **4** | 3 docs | R3.1, R3.2, R3.3, R3.4 | `CLAUDE.md`, `README.md`, `src/kayak/cli/fetch_usgs_ogc.py`, `CHANGELOG.md`, `docs/done/PLAN_round4_remediation.md` | existing `test_changelog_facts` | — |
| **5** | 4 cov | R4.2, R4.3, R4.4, R4.5 | `tests/test_committed_reach_geom.py` (new), `Makefile`, `tests/test_scripts/test_migration_csv_reconciliation.py`, `tests/php/ConfigTest.php`, `.github/workflows/ci.yml` | the new/extended tests | — |
| **6** | 4 cov | R4.1 | `tests/js/feature_map.spec.ts` (new) | the Playwright case | — |

**Sequencing.** PR 1 → PR 3 is the one hard edge (R2.1's guard parses round-4's R1.1 `grep -c … → 0`
Verify, which only passes once PR 1 deletes the line). PRs 2/4/5/6 are independent. **CI-config contention:**
PR 1 and PR 5 both touch `ci.yml` (different hunks — a new lint-misc step vs. a `KAYAK_LEVELS_BIN` export);
if both are open at once, rebase PR 5 on PR 1. Don't open all six simultaneously.

---

## PR 1 — db_push.sh ops fixes + trap guard (R1.1, R1.2, R1.5)

**Edits to `scripts/db_push.sh`** (all inside the `<<'REMOTE'` heredoc, which runs on prod Debian bash 5):

1. **R1.5** — `:96–97`, replace the predictable `/tmp` paths (leave `REPLACED_GZ` `:98`, it's in `BACKUP_DIR`):
   ```sh
   LIVE_FINAL="$(mktemp)"
   NEW_DB="$(mktemp)"
   ```
2. **R1.2** — right after the stop loop (`:106`), define the helper + arm the trap:
   ```sh
   restart_timers() {
       local u
       for u in kayak-pipeline.timer kayak-decimate.timer \
                kayak-backup-weekly.timer kayak-backup-hourly.timer; do
           sudo -n systemctl start "$u" || true
       done
   }
   trap restart_timers EXIT
   ```
3. **R1.1** — delete `:134` `DELETE FROM pages;` (the table was dropped by `0006`; sole repo consumer).
4. **R1.2** — integrity-failure branch (`:145–148`): drop the manual restart loop, leaving the bare
   `exit 1` (`:149`) — the trap now covers it.
5. **R1.2** — success path (`:176–179`): replace the explicit restart loop with `restart_timers` (keep the
   `echo "--- Restarting timers ---"`), then disarm: `trap - EXIT`. This DRYs the 4-timer list into the one
   helper and restarts exactly once on a clean run.

   *Net trap semantics (verify by reading the diff): any exit after the stop — `set -e` abort, the R1.1
   abort site, integrity `exit 1`, or a mid-swap failure — fires `restart_timers`; a clean run restarts
   explicitly then disarms. No path leaves the 4 timers stopped.*

**New `scripts/check-db-push-trap.sh`** (style = `check-phpstan-level.sh`; a CI grep guard, not `bats`):
```sh
#!/usr/bin/env bash
# Guard (round-5 R1.2): db_push.sh must restart the pipeline/backup timers on ANY
# failure between the stop block and the restart — i.e. a `trap … EXIT` must be armed.
# The round-4 review found this missing; the fix is only durable if CI enforces it.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! grep -qE '^\s*trap restart_timers EXIT' scripts/db_push.sh; then
    echo "ERR: scripts/db_push.sh is missing 'trap restart_timers EXIT' — a failure" >&2
    echo "between the timer-stop and restart would strand prod timers (round-5 R1.2)." >&2
    exit 1
fi
# Non-vacuity belt: the DELETE-FROM-pages breaker (R1.1) must stay gone.
if grep -q 'DELETE FROM pages' scripts/db_push.sh; then
    echo "ERR: 'DELETE FROM pages' is back in db_push.sh (R1.1 regressed)." >&2
    exit 1
fi
echo "OK: db_push.sh trap guard present, no DELETE FROM pages."
```
*(Note: this guard's R1.1 grep duplicates round-4 R1.1's Verify; that's fine — PR 3's `test_remediation_claims`
is the general mechanism, this is the targeted regression belt for the specific incident.)*

**`.github/workflows/ci.yml`** — add a step in the `lint-misc` job after "systemd unit validation" (`:111`):
```yaml
      - name: db_push.sh restart-trap guard
        run: scripts/check-db-push-trap.sh
```
`scripts/*.sh` is already in the `shellcheck --severity=warning` scope (`ci.yml:90`) — the new check script
must pass shellcheck. The trap edit is inside `<<'REMOTE'` (shellcheck-opaque), so it stays green (verified
in the plan's red-team).

**Gate:** `shellcheck scripts/db_push.sh scripts/check-db-push-trap.sh`; `bash scripts/check-db-push-trap.sh`
→ OK; `grep -c 'DELETE FROM pages' scripts/db_push.sh` → 0.
**Verify (manual, off-prod):** on a sandbox copy of the heredoc, inject `false` after the stop block →
the 4 timers are `active` afterward (the dev Mac has no systemd; this is a prod/staging check).

---

## PR 2 — source_url tab filter (R1.3)

**`php/includes/source_url.php`:**
- `:20` docstring: `rejects any CR/LF/NUL` → `rejects any CR/LF/TAB/NUL`.
- `:32`: `preg_match('/[\r\n\0]/', $raw)` → `preg_match('/[\r\n\t\0]/', $raw)`.

**`tests/php/SourceUrlTest.php`** — add to `test_dangerous_schemes_rejected()` (`:72–83`, the scheme test;
*not* the CRLF/header-injection test at `:85`):
```php
        // Tab/control char defeats parse_url's scheme check; browsers strip
        // TAB/LF/CR per WHATWG → live javascript:/data: on click. Rejected outright. (round-5 R1.3)
        $this->assertSame('', sanitize_source_url("j\tavascript:alert(1)"));
        $this->assertSame('', sanitize_source_url("da\tta:text/html,<script>alert(1)</script>"));
```

**Gate:** `composer test -- --filter SourceUrlTest` green; `composer analyse` (PHPStan L9) clean;
`composer fix-check`. **Verify:** the two new asserts pass; revert the `\t` → they fail.

---

## PR 3 — the claim-vs-source guard (R2.1) — DEPENDS ON PR 1

**New `tests/test_remediation_claims.py`** (style = `tests/test_changelog_facts.py`). Structure:
```python
"""Guard (round-5 R2.1): every mechanically-checkable Verify in an archived
remediation plan must still pass against HEAD — so no plan can record a fix as
done while the source disagrees (round-5 headline: round-4's R1.1/R1.2/R1.3 were
archived 'shipped' but never landed). Only the `grep -c '<lit>' <path> → N` subset
is mechanizable; prose Verifies stay manual (each fix ships its own test).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PLANS = sorted((_ROOT / "docs" / "done").glob("PLAN_round*_remediation.md"))  # docs/done ONLY

# A Verify FIELD spans a BARE **Verify:** → the effort marker **( (DOTALL, so a wrapped
# continuation-line command stays inside the field). The (?<!`) excludes backtick-wrapped
# prose mentions of `**Verify:**` — THIS plan, being about the parser, contains them.
_FIELD = re.compile(r"(?<!`)\*\*Verify:\*\*(.*?)(?:\*\*\(|\n- \*\*R|\Z)", re.DOTALL)
# A runnable command: `grep -c '<literal>' <path>` → <N>. Literal patterns only
# (Python substring == grep -Fc, not BRE).
_RUNNABLE = re.compile(r"`grep -c '([^']*)' (\S+?)`\s*(?:→|->)\s*`?(\d+)`?")
# A genuine command ATTEMPT: grep -c + a quote (vs a bare prose mention of the token).
_ATTEMPT = re.compile(r"`grep -c\s+['\"]")
_BRE_META = {".", "*", "[", "]", "^", "$", "\\"}


def _fields() -> list[tuple[str, str]]:
    return [(p.name, f) for p in _PLANS for f in _FIELD.findall(p.read_text("utf-8"))]


def _count(pattern: str, path: str) -> int:  # == grep -Fc: substring line-count
    return sum(1 for line in (_ROOT / path).read_text("utf-8").splitlines() if pattern in line)


def test_archived_grep_verifies_pass() -> None:  # the core claim-vs-source check
    ran = 0
    for plan, field in _fields():
        for pattern, path, n in _RUNNABLE.findall(field):
            assert not (set(pattern) & _BRE_META), f"{plan}: non-literal pattern {pattern!r}"
            got = _count(pattern, path)
            assert got == int(n), f"{plan}: `grep -c {pattern!r} {path}` = {got} != {n}"
            ran += 1
    assert ran, "no runnable grep -c Verify found — parser regressed?"


def test_no_unparsed_grep_c_attempt() -> None:  # counter (b): no silent under-coverage
    unparsed = []
    for plan, field in _fields():
        for m in _ATTEMPT.finditer(field):
            if not _RUNNABLE.match(field, m.start()):  # the attempt didn't fully parse
                unparsed.append((plan, field[m.start() : m.start() + 60]))
    assert not unparsed, f"unparsed grep -c command attempts: {unparsed}"


def test_guard_is_non_vacuous() -> None:  # R1.1 must be present + passing
    patterns = [pat for _, f in _fields() for pat, *_ in _RUNNABLE.findall(f)]
    assert any("DELETE FROM pages" in p for p in patterns), "R1.1 grep-Verify not found"
```
**Why block-extraction + the `(?<!`)` lookbehind matter (pass-2 fixes, both empirically validated against
round-3/4 + the archived round-5 plan):** **(1)** the round-5 plan *wraps* R1.1's Verify — `**Verify:**` on
one line, the `grep -c …` command on the next — so a per-line filter would drop it and silently uncover the
claim once archived; `_FIELD` captures the whole field (DOTALL). **(2)** this plan, being *about* the
Verify-parser, contains the literal `**Verify:**` token in its design prose ("Parse each `**Verify:**` body
field"); without the lookbehind, `_FIELD` would open a field there and swallow the design-prose
`grep -c '<pattern>'` examples, tripping counter (b) against its own archived plan — **the third
self-reference trap this round** (after the level-8 guard and the in-flight glob). The lookbehind excludes
the backtick-wrapped mentions (real labels are bare). **Validated:** the only runnable across the three
plans is R1.1 (`= 1 ≠ 0` now → RED; → 0 after PR 1 deletes the line → green), and counter (b) = 0.

**Gate:** after PR 1 is merged, `pytest tests/test_remediation_claims.py -q` green; `ruff check`/`ruff format
--check`/`mypy` on the new file. **Verify:** re-add `DELETE FROM pages` on a scratch copy → RED; a deliberately
wrong `→ N` → RED.

---

## PR 4 — data audit-trail + doc drift (R3.1, R3.2, R3.3, R3.4)

- **R3.1** `CLAUDE.md:207`: extend the exceptions note — `**`reach.geom` and `reach.gradient_profile` are the
  documented exceptions**` → add `reach.huc`, with the rationale that it is `levels assign-huc`-derived
  (deterministic point-in-polygon over WBD), *snapshot-carried in `reach.csv`* (it diffs cleanly, unlike the
  geom blob), so a tool-run that changes it is not the "hand backfill via migration" the convention governs.
  Also widen the `feedback_migration_over_db_push` memory's scope note to "hand edits."
- **R3.2** sweep the `usgs_id`-as-selection wording (source-keyed since #75):
  - `CLAUDE.md:82` and `CLAUDE.md:171`, `README.md:90`: "for gauges with `usgs_id`" → "for gauges **linked to
    a USGS source**."
  - `src/kayak/cli/fetch_usgs_ogc.py:3` header docstring ("all gauges with a usgs_id") + `:75` `--site` help —
    reword to match the authoritative `_build_site_map` docstring (`:87–88`). **Docstring/help only — no logic;
    ruff/mypy unaffected.** Leave `docs/PLAN_add_gauges_reaches.md` / `docs/PLAN_montana_gauges.md:84` (historical
    plan rationale, by design).
  - **Verify:** `grep -rn "gauges with .*usgs_id\|all gauges with a usgs_id" CLAUDE.md README.md src/` → nothing.
- **R3.3** `CHANGELOG.md` — add a thematic `[Unreleased]` entry for the source-based USGS-OGC fetch refactor +
  Batch A/B/C. **Prose only — do not put a shipped `R<n>.<n>`+`#<pr>` next to an open-status word** (the one
  thing `test_changelog_facts.py` forbids). **Verify:** `pytest tests/test_changelog_facts.py` stays green.
- **R3.4** `docs/done/PLAN_round4_remediation.md` — prepend an **append-only erratum** (do not rewrite the body):
  > **Erratum (round 5, #&lt;PR&gt;):** R1.1/R1.2/R1.3 were recorded shipped/⏳ here but never reached the
  > repo; they land in round 5 — see `project-review-5/`.

  **Must not** introduce a `**Verify:**` field or a quoted `grep -c` span (so PR 3's guard keeps passing
  against this file). Its R1.1 `grep -c … → 0` Verify already holds once PR 1 lands.

**Gate:** `pytest tests/test_changelog_facts.py tests/test_doc_plans_filed.py tests/test_schema_doc_sync.py -q`;
`ruff`/`mypy` (the fetch_usgs_ogc.py docstring touch); the R3.2 grep returns nothing.
**Watch:** R3.4 edits an archived `docs/done/PLAN_round4_*`. The erratum's placeholder `#<PR>` isn't a
`#\d+`, so in PR 4 it changes the `test_changelog_facts` "shipped" set by nothing; at the *final archival
step* (when `#<PR>` becomes a real number) it adds **R1.2** to that set (R1.1/R1.3 are already shipped via
the round-4 `#70` header line) — harmless, since round-5 is closed and the CHANGELOG names no open R1.x.

---

## PR 5 — CI / test coverage (R4.2, R4.3, R4.4, R4.5)

- **R4.3** `Makefile:1–2` — add the real-but-unlisted targets to `.PHONY`: append
  `test-php init-db install help` (verify each exists; `init-db`/`install`/`help` are further down the file).
- **R4.4** `tests/test_scripts/test_migration_csv_reconciliation.py:38` — broaden `_SOURCE_INSERT` to also
  capture the `VALUES ('<name>'` form, **or** add `test_no_source_insert_uses_values_form` asserting no
  `INSERT INTO source … VALUES` exists (all 32 current wiring INSERTs use `SELECT`, so either is forward-
  looking). Prefer the assertion — simpler, and it documents the convention. Keep the non-vacuity test.
- **R4.5** make the emit-config parity test fail-not-skip when `levels` is *expected*:
  - `tests/php/ConfigTest.php:153–159` — when `KAYAK_LEVELS_BIN` is **set but not executable**, `$this->fail(...)`
    instead of `markTestSkipped`; only skip when the env is unset *and* the resolver returns null:
    ```php
    $env = getenv('KAYAK_LEVELS_BIN');
    $envSet = is_string($env) && $env !== '';
    $bin = $envSet ? $env : FunctionalTestCase::resolveVenvCommand(dirname(__DIR__, 2));
    if ($bin === null || !is_executable($bin)) {
        if ($envSet) { $this->fail("KAYAK_LEVELS_BIN='$env' set but not executable"); }
        $this->markTestSkipped('no `levels` CLI (KAYAK_LEVELS_BIN, prod venv, .venv, or PATH)');
    }
    ```
  - `.github/workflows/ci.yml` — in `lint-misc`, right after "Install kayak package (for `levels init-db`)"
    (`:141–142`), export the resolved path so the env is explicit (not an implicit PATH-ordering artifact):
    ```yaml
      - name: Pin levels for the emit-config parity test (R4.5)
        run: echo "KAYAK_LEVELS_BIN=$(command -v levels)" >> "$GITHUB_ENV"
    ```
    Now `testEmitConfigJsonRoundTripsViaConfig` *runs* (env set + executable), and *fails* (not skips) if a
    future `ci.yml` edit drops the `pip install -e .`.
- **R4.2** **new `tests/test_committed_reach_geom.py`** — assert `kayak.cli.check_reaches.scan_for_issues`
  flags nothing over the committed snapshot's real 420 reaches (guards the dev-only-regenerable geom at merge —
  the prod pipeline soft-fail is the only check today). **Mechanism (pass-2 — load-bearing):**
  `scan_for_issues(database_url=…)` opens its *own* engine via `get_session(url)`/`create_engine(url)`, so a
  conftest in-memory `:memory:` engine is unreachable (a second `create_engine` on the URL opens a *separate*
  empty DB → false green). Use a temp **file** DB: (1) `Base.metadata.create_all` (or `levels init-db --no-seed`)
  against `sqlite:///<tmpfile>`; (2) load the committed snapshot with `scripts/import_metadata.py`'s loaders —
  `_load_csvs` + `_apply_geom` **and `_apply_gradient`** (loading `reaches-gradient.json` too exercises the
  extreme-peak check at `check_reaches.py:177–197`, matching prod's `check-reaches`) against
  `sqlite3.connect(<tmpfile>)`; (3) `scan_for_issues(database_url="sqlite:///<tmpfile>")` → assert
  `flagged == 0`. *(Red-team ran exactly this end-to-end on the committed data: total=420, flagged=0.)*
  **Verify:** passes on the snapshot; a hand-broken geom endpoint fails it.

**Gate:** `pytest tests/test_committed_reach_geom.py tests/test_scripts/test_migration_csv_reconciliation.py -q`;
`make check` (exercises the new `.PHONY` targets); `composer test -- --filter ConfigTest` (confirm it *runs*,
not skips, with `KAYAK_LEVELS_BIN` set locally).

---

## PR 6 — #79 map popup behavioral test (R4.1)

**New `tests/js/feature_map.spec.ts`** (Playwright; harness = `tests/js/global-setup.ts` boots
`levels init-db`→`build`→`php -S` at `127.0.0.1:8000`; pattern from `editor.spec.ts`'s `sqliteExec`/`seedReach`):

1. **Seed a reach with a Put-in coordinate** (load-bearing: a coords-less reach renders no map). Via
   `/description.php?id=<id>`, a start coordinate alone lights up the map — `php/includes/description_detail.php:464–468`
   puts `latitude_start`/`longitude_start` into `$map_points['Put-in']`; `:506` (`count($map_points) >= 1`) →
   `:509` `gm_render_map()` emits `#feature-map` (`php/includes/gauge_map.php:120`) + the `leaflet.js` +
   `feature-map.js` tags (`description_detail.php:84–88`). **No geom needed** — the `_render_reach_map`
   `[false,'']` path is the *`/reach.php`* route, not this one. Seed (every other `reach` column is nullable or
   `server_default 0`):
   ```ts
   sqliteExec(`INSERT INTO reach (name, sort_name, display_name, river, latitude_start, longitude_start, no_show)
     VALUES ('R4.1 Map ${stamp}', 'r4.1 map ${stamp}', 'R4.1 Map', 'Test River', 44.06, -121.31, 0);
     SELECT last_insert_rowid();`);
   ```
2. Navigate to `/description.php?id=<id>`; `await page.waitForSelector('.leaflet-container')` (Leaflet works in
   the harness — `smoke.spec.ts` asserts it on `/map.html`; the `php -S` harness emits no CSP header, so the
   Leaflet `<script src=>` tags load).
3. Right-click: `await page.locator('.leaflet-container').click({ button: 'right' })` — `feature-map.js:503`
   listens via `L.DomEvent.on(map.getContainer(), 'contextmenu', …)` (native, on the container, so a bubbled
   `contextmenu` fires it).
4. Assert `.latlon-popup` is visible with a `\d+\.\d+` coordinate; optionally that the Copy button exists.
   (Clipboard *read* needs a permissions grant — assert the button/text, not the paste.)

**Effort note:** the heaviest item (new spec + headless map render), but the route/seed are now resolved from
source (no spike). Keep it one focused spec.

**Gate:** `npm test` (Playwright) green locally; the case fails if the `feature-map.js` `contextmenu` handler
is stubbed. **Verify:** comment out the `contextmenu` handler → RED.

---

## Per-PR definition of done

1. Branch off fresh `origin/main` in a worktree. 2. Make the edits + the shipped guard/test. 3. **Full local
gate green** (`make check` + the PR's specific Verify). 4. Commit (typed, with `Co-Authored-By`). 5. Push, open
PR, CI green. 6. Merge; `git pull` on the live `main`. 7. Run the prod-side **Verify** where applicable
(R1.2 timer-restart on staging; R3.2 grep; etc.). 8. `git worktree remove`.

After all six land, archive `project-review-5/{REVIEW,PLAN,IMPL}_round5*` → `docs/done/` (as #72 did for round
4), index them, and add the `#PR` numbers to R3.4's erratum and the CHANGELOG.

## Version log

- **v1** — initial execution playbook drawn from the converged remediation plan.
- **v2** — pass-1 convergence (3 cold lenses vs. source; each sketch traced/run). **R2.1 parser rewritten:**
  the per-line `**Verify:**` filter dropped commands on wrapped continuation lines (round-5's own R1.1 would
  be silently uncovered once archived) → extract the Verify field as a DOTALL block; ruff-cleaned the sketch
  (E741/SIM905). **R4.2 mechanism corrected:** `scan_for_issues` opens its own engine, so the conftest
  in-memory `:memory:` engine is unusable → temp file DB + `import_metadata` loaders (incl. `_apply_gradient`),
  verified 420/0 end-to-end. **R4.1 simplified:** the "renderer spike" is resolvable from source — start-coords
  alone render the map via `/description.php`; detail-handler paths corrected to `php/includes/`. Plus the R3.4
  "Watch" wording (only R1.2 is newly-shipped, at archival) and the R3.2 `:87–88` ref. *PR 1's db_push.sh trap
  edit + check-script and PRs 2/4/5(R4.3/R4.4/R4.5) verified clean; lens C found completeness + sequencing
  sound.*
