# Plan — C901 cleanup for the grandfathered scripts

**Status:** Closed (2026-05-12, verified clean 2026-05-15). All phases shipped:

- **Plan check-in** — commit `629e97c` (2026-05-11).
- **Phase 1** — `529561e` (parsers + utils + huc, 6 functions → cc≤8).
- **Phase 2** — `74eb9bc` (`fetch_usgs_ogc`, 2 functions → cc≤7).
- **Phase 3a** — `1dd03e5` (`fetch` cc 30 → ≤7).
- **Phase 3b** — `a7b13c9` (`calc_rating` cc 22 → ≤7).
- **Phase 3c** — `8eacf1a` (`calculator` cc 30 → ≤8 + `# noqa: C901` on `_safe_eval`/`_eval`; closed the `cli/*` per-file-ignore).
- **Tracing carve-out** — `b662118` followed by polish in `3d4786c` (2026-05-12). `find_huc4` and `trace_reach` split into four helpers (`_scan_dir_for_huc4` — later replaced by `_nearest_huc4_in_dir` in #23's nearest-flowline rework — `_resolve_huc4`, `_extend_and_trim_path`, `_load_missing_geoms`); both land at cc≤5. Regression tests in `tests/test_tracing/test_trace.py` (Sandy reach ground truth).

**End state re-verified 2026-05-15 against `main` at `804b02d`:**
- `ruff check src/ --select C901 --config 'lint.per-file-ignores={}'` → **All checks passed!** (0 hits even with overrides bypassed).
- `[tool.ruff.lint.per-file-ignores]` stanza is **removed entirely** from `pyproject.toml` — exceeds the plan's target end-state ("retains `tracing/*`"); the tracing closeout dropped that last entry too.
- The two `# noqa: C901` markers remain on `cli/calculator.py:50` (`_safe_eval`) and `:67` (`_eval`) per § Decisions baked in.

**Residual deferred work:** type annotations for `kayak.tracing.*`. The `[tool.mypy.overrides]` carve-out stays. The original plan deferred tracing pending "tests + type annotations + complexity in one PR" — `b662118` shipped tests + complexity; annotations remain a separate follow-on (out of scope for this plan).

The original draft Context / Why / Decisions baked in / Out of scope / Reproduce sections are preserved below as the historical record of how the work was sequenced.

> **Cross-check:** plan drafted 2026-05-11 against `main` at `2f39e15` (after the build.py split landed). Dates absolute. References are `file:line:function` against the `main` at draft time.

## Context

The build.py split (PR commits `7281e74`..`2f39e15`) added `C901` to `ruff.lint.select` to gate new complexity in `kayak.web.build`. To keep the build.py PR small, 11 files outside `web/build/` were grandfathered in `[tool.ruff.lint.per-file-ignores]` — `src/kayak/cli/*`, `parsers/*`, `tracing/*`, `huc/*`, `utils/*`. Those grandfather entries hide 15 cc>10 functions across 11 files. This plan refactors 10 of them in 3 phases, marks 2 with `# noqa: C901` for structural reasons, leaves 2 in `tracing/*` as deferred work (mirroring the existing mypy override on that module), and shrinks the `per-file-ignores` stanza tier-by-tier so the gate applies everywhere except `tracing/*` when the plan completes.

**End state:** `pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]` retains only `"src/kayak/tracing/*"` (paired with the existing `[tool.mypy.overrides]` for `kayak.tracing.*`). Every other directory enforces `C901` repo-wide. All 13 in-scope cc>10 hits are either refactored to cc≤10 or carry an inline `# noqa: C901` marker with a one-line rationale.

**Relationship to `docs/PLAN_production_discipline.md`:** independent. C901 is code-quality housekeeping; production discipline is uptime / monitoring / deploys. The two plans share no files and can be sequenced or parallelized freely. If production discipline lands a deploy-from-CI Tier 3, this plan benefits from the same green-CI gate.

## Why

`C901` only works as a regression gate if it's actually enforced. Today it's enforced in `web/build/` only — every other directory can re-grow complexity without anyone noticing. The grandfather entries are a "we'll come back to this" signal that decays fast: by the time they bite, the original context for *why* a function is at cc=30 is lost and the refactor is twice as risky.

The 15 hits aren't equally urgent. Three are pipeline-critical (`fetch` cc=30, `calculator` cc=30, `calc_rating` cc=22 — they run hourly via systemd timers); the rest are parsers, offline utilities, and a tracing module that pre-dates the strict-typing regime. A phased rollout lets us prove the extraction patterns on the low-risk cases before touching the hourly pipeline. Tracing is deferred along the same boundary as its existing mypy override.

Goal: cc≤10 on all in-scope functions, `[tool.ruff.lint.per-file-ignores]` shrunk to just `tracing/*` (mirroring the existing mypy carve-out), no behavior change.

## Constraints (assumptions stated by current state)

- **Per-function refactor only — no module splits.** The smallest in-scope file (`parsers/nwps.py`) is 97 lines; the largest in-scope file (`utils/http_client.py`) is 431. None are anywhere near the 2187-line monolith that warranted splitting `cli/build.py`.
- **Three phases, low risk first.** Each phase shrinks `per-file-ignores` in `pyproject.toml` as a directory becomes clean. Phases are independently mergeable; each commit reverts cleanly without affecting the others (no data migrations, no cross-phase deps).
- **No new test framework / fixtures.** Existing `tests/test_*.py` files are the regression net. One exception: two pinned tests added to `tests/test_cli/test_calc_rating.py` in Phase 3 (rationale below).
- **Pipeline-criticality drives the verification gate.** Low-risk phases pass on `pytest` alone. Medium/high-risk phases also run a same-code DB-state diff against the live `kayak.db` snapshot.
- **Test-coverage signal is module-level, not function-level.** Per-file `test_*.py` counts (verified): `test_fetch.py:15`, `test_calculator.py:8`, `test_calc_rating.py:5`, `test_fetch_usgs_ogc.py:8`, parser tests 7–11 each, `test_http_client.py:39`, `test_huc/test_assign.py:10`. **No tests exist for `tracing/trace.py`** — a primary reason it stays out of scope.

## Decisions baked in

- **`_safe_eval` (cc=14) and `_eval` (cc=13) in `cli/calculator.py` → `# noqa: C901` with rationale comment, not extracted.** They are an `ast.AST` visitor — each `isinstance(node, ast.X)` is a leaf case with no shared logic. Extracting `_eval_constant`, `_eval_binop`, etc. would inflate the call surface from one helper to seven, force `lookup` (closure variable) into every signature, and not actually reduce total complexity — just move it under McCabe's per-function threshold while making the dispatch table harder to read. This is the canonical case the `noqa` mechanism exists for.

- **`trace_reach` (cc=15) and `find_huc4` (cc=11) in `tracing/trace.py` → out of scope.** The module has a mypy override in `pyproject.toml` (lines 92–98) describing it as "a wholesale move of a standalone script that pre-dates the strict-typing regime; typing it gradually rather than front-loading 19+ annotations." Critically, **the module has zero tests** — no `tests/test_tracing/` directory exists; the only reference outside `src/` is `scripts/extract_trace_data.sh` which uses `trace_reach` for offline data preparation. A C901 refactor with no regression net is not the right place to first integrate it. Defer until the module is owned strictly: add tests, type annotations, and complexity refactor in the same PR, not piecemeal. Per-file-ignores keep `"src/kayak/tracing/*" = ["C901"]` paired with the mypy override.

- **`calc_rating` gets two pinned tests added in Phase 3 before extraction.** It has 5 tests today, all happy path. The proposed refactor collapses a 3-way conditional to two parallel "fill missing" calls (see Phase 3 sketch); two specific failure modes are not covered by current tests and the refactor could regress them:
  1. *Both-exist pre-loop time-set invariant* — newly-stored observations must not leak into the in-loop time set used by the parallel calls.
  2. *Out-of-range value=None path* — gauge value outside the rating table currently produces no flow row; the refactor must preserve that.

- **Each phase keeps the function's public signature.** Helpers are file-private (`_lower_snake_case`); no callers outside the file change.

- **No same-code control-build idiom from the build.py split applies here.** That gate worked because the output was deterministic HTML modulo timestamps. Here the "output" is database state, which is not deterministic across two runs (observations land continuously). The Phase 2/3 verification gate is *delta-pattern equivalence*, not byte equality: a refactor that introduces a regression should change the *kinds* of rows written, not just the timestamps. See the per-phase **Verification gate** subsections below.

## Target shape

Before / after (cc target shown; "noqa" means the function stays as-is with an inline marker; "deferred" means out of scope for this plan):

| File | Function | cc now | cc after | Phase |
|---|---|---|---|---|
| `parsers/nwps.py:36` | `parse` | 13 | ≤7 | 1 |
| `parsers/nwrfc_xml.py:87` | `_parse_observed` | 13 | ≤4 | 1 |
| `parsers/usace_cda.py:43` | `parse` | 11 | ≤6 | 1 |
| `parsers/wa_gov.py:40` | `parse_line` | 18 | ≤6 | 1 |
| `utils/http_client.py:353` | `async_fetch_many` | 11 | ≤6 | 1 |
| `huc/assign.py:168` | `run` | 13 | ≤8 | 1 |
| `cli/fetch_usgs_ogc.py:125` | `_fetch_continuous` | 14 | ≤8 | 2 |
| `cli/fetch_usgs_ogc.py:226` | `fetch_usgs_ogc` | 11 | ≤7 | 2 |
| `cli/fetch.py:113` | `fetch` | 30 | ≤7 | 3 |
| `cli/calc_rating.py:32` | `calc_rating` | 22 | ≤7 | 3 |
| `cli/calculator.py:104` | `calculator` | 30 | ≤8 | 3 |
| `cli/calculator.py:48` | `_safe_eval` | 14 | noqa | 3 |
| `cli/calculator.py:58` | `_eval` | 13 | noqa | 3 |
| `tracing/trace.py:60` | `find_huc4` | 11 | deferred | — |
| `tracing/trace.py:362` | `trace_reach` | 15 | deferred | — |

After all 3 phases:
- `[tool.ruff.lint.per-file-ignores]` retains only `"src/kayak/tracing/*" = ["C901"]` (matching the existing mypy carve-out).
- `calculator.py`'s two `# noqa: C901` markers are the only in-source suppressions.
- 11 of 13 in-scope functions land at cc≤8; the other 2 are explicit `noqa`.

## Migration phases

Three phases, **six commits**, low risk first so the extraction approach is battle-tested by the time it reaches the hourly pipeline:

1. Phase 1 — one commit bundling all six low-risk refactors (parsers / utils / huc).
2. Phase 2 — one commit bundling both `fetch_usgs_ogc.py` refactors.
3. Pre-Phase-3 — one commit adding the two pinned `calc_rating` regression tests.
4. Phase 3a — `fetch.py` refactor.
5. Phase 3b — `calc_rating.py` refactor.
6. Phase 3c — `calculator.py` refactor + `# noqa: C901` markers on `_safe_eval` / `_eval`.

Each commit is independently mergeable and revertable; the verification gate runs after every commit, not just at phase boundaries. **Push and wait for CI green between commits** — same pattern as the build.py split. Don't stack uncommitted phases; a single bad commit between green ones lets `git bisect` find the regression in O(log n).

Plan-doc location: this file should be checked in as `docs/PLAN_c901_cleanup.md` before Phase 1 starts, matching the `PLAN_build_split.md` / `PLAN_production_discipline.md` precedent — keeps the **Reproduce** commands reachable from any session that picks the work up.

### Phase 1 — Low-risk refactors (6 functions, 6 files)

All pure logic or thin orchestrators with parser-level test coverage. Sketches name the helper and cite its body's line range; signatures are illustrative, not contracts.

- **`parsers/nwps.py:36 parse` (cc=13)** — extract `_extract_stage_and_flow(entry) -> tuple[float | None, float | None]` for the primary/secondary unit dispatch (~ lines 70–81). Outer `parse` loops sites and dispatches.
- **`parsers/nwrfc_xml.py:87 _parse_observed` (cc=13)** — the function is three near-identical elif branches (stage / discharge / inflow), each doing `units check + safe_float + isfinite + dump_to_db`. Either: extract `_emit_observation(station, when, elem, *, data_type, valid_unit_substrings, require_non_negative)` (~lines 98–115) and reduce the loop to 3 short calls; **or** introduce a module-level `_TAG_HANDLERS: dict[str, tuple[DataType, tuple[str, ...], bool]]` table and a single dispatch helper. Either drops cc to ≤4.
- **`parsers/usace_cda.py:43 parse` (cc=11)** — extract `_extract_observation_from_entry(entry, station)` for the innermost JSON loop (~ lines 75–87).
- **`parsers/wa_gov.py:40 parse_line` (cc=18)** — this is a state machine (state 0/1/2) and the state-2 data-row body is the cc driver. Extract `_emit_data_row(line, parts) -> None` for the state-2 body (~ lines 74–101: station-and-len check, "No Data", quality bounds, time parse, value parse + finite, temperature conversion, dump). The state-machine dispatch stays in `parse_line`.
- **`utils/http_client.py:353 async_fetch_many` (cc=11)** — the cc lives in the budget-aware await block and the per-task result collection, **not** the semaphore setup (which closes over a dict, making it awkward to extract). Extract `_await_with_budget(tasks: dict[str, asyncio.Task], deadline: float | None) -> None` (~ lines 404–417) and `_collect_task_results(url_to_task) -> dict[str, FetchResult]` (~ lines 419–428).
- **`huc/assign.py:168 run` (cc=13)** — extract `_assign_huc_to_reach(reach, tree, codes, huc8_map)` for the per-reach lookup + conditional update (~ lines 210–258).

**After:** drop `"src/kayak/parsers/*"`, `"src/kayak/utils/*"`, `"src/kayak/huc/*"` from `[tool.ruff.lint.per-file-ignores]`.

**Verification gate:**
- `ruff check src/ tests/` — must be clean now that `parsers/*`, `utils/*`, `huc/*` per-file-ignores are gone.
- `ruff format --check src/ tests/`.
- `mypy src/` — pre-existing `types-requests` warning is unchanged.
- `pytest -q` — same pass count as `main` (use `pytest --collect-only -q` to quote `collected / deselected / skipped` numbers in the commit message, not just `N passed`).

### Phase 2 — Medium-risk refactors (2 functions, 1 file)

DB-touching but well-isolated, with 8 tests in `tests/test_cli/test_fetch_usgs_ogc.py`:

- **`cli/fetch_usgs_ogc.py:125 _fetch_continuous` (cc=14)** — extract `_extract_observation_from_feature(feature, source_id, data_type) -> tuple[datetime, float] | None` for the innermost JSON parsing (~ lines 168–205).
- **`cli/fetch_usgs_ogc.py:226 fetch_usgs_ogc` (cc=11)** — extract `_update_latest_and_gauge_cache(session, source_data_type_pairs)` for the pair-iteration cache refresh (~ lines 272–283).

**After:** `cli/*` still grandfathered (Phase 3 hits remain); `tracing/*` stays grandfathered (deferred).

**Verification gate:** Phase 1 gate plus a single-run DB diff for `fetch-usgs-ogc`. Set `KAYAK_DB` to the live SQLite path first (`~/tpw/DB/kayak.db` on dev macOS, `/home/pat/DB/kayak.db` on prod Debian).

```bash
export KAYAK_DB=~/tpw/DB/kayak.db   # or /home/pat/DB/kayak.db on prod

# Snapshot the live DB
cp "$KAYAK_DB" /tmp/p2-before.db
# Run the subcommand once against the snapshot
DATABASE_URL=sqlite:////tmp/p2-before.db levels fetch-usgs-ogc
sqlite3 /tmp/p2-before.db ".dump observation latest_observation latest_gauge_observation" \
  > /tmp/p2-before.sql

# (apply phase 2 commit)

cp "$KAYAK_DB" /tmp/p2-after.db
DATABASE_URL=sqlite:////tmp/p2-after.db levels fetch-usgs-ogc
sqlite3 /tmp/p2-after.db ".dump observation latest_observation latest_gauge_observation" \
  > /tmp/p2-after.sql

# Expected pattern: row counts equal pre/post (no rows dropped or duplicated by
# the refactor); content differences only on rows whose observed_at fell inside
# the fresh-fetch window between snapshot and re-run. Run the snapshot diff
# twice with the *same* code (between the two `cp` lines above) once to
# characterize the time-driven drift baseline before trusting a post-refactor
# diff. Refactor-clean = post diff matches the same-code baseline.
diff /tmp/p2-before.sql /tmp/p2-after.sql | wc -l
```

### Phase 3 — High-risk refactors (3 functions, 3 files; pipeline-critical)

These are the hourly-pipeline functions. Each gets careful handling per the Plan-agent risk flags:

#### 3a. `cli/fetch.py:113 fetch` (cc=30 → ≤7) — 15 tests in `test_cli/test_fetch.py`

The function is **already** structured with `--- Phase 1/2/3 ---` banner comments. Extract along those banners:

- `_filter_yaml_sources(yaml_sources, parser_filter, url_filter) -> list[dict]` — lines 140–143.
- `_prepare_work_items(session, yaml_sources, args) -> list[_FetchWork]` — lines 154–211. **Session passed in, not acquired in the helper.**
- `_fetch_content(work_items, args) -> dict[str, str | None]` — lines 216–246. **No DB session held during `asyncio.run`.**
- `_parse_and_store(session, work_items, content_map, args) -> None` — lines 251–304.

**Risk flags (preserve in refactor):**
- The two `try/finally: session.close()` blocks at lines 149/213 and 250/307 are **correctness**, not cleanup polish — Phase 1's session must close before async fetch begins (Phase 2 must not hold a DB connection across `asyncio.run`). Keep session creation in `fetch()`; helpers receive sessions, never create them.
- The two `except` branches at lines 289 and 293 differ only in log level (`logger.error` for expected `ValueError`/`KeyError`/`LookupError`; `logger.exception` with traceback for everything else). **Do not merge.** Test `test_fetch_continues_after_unexpected_parser_error` pins this.
- `parser_cls` is looked up at both lines 180 and 260 — intentional pre-flight (skip unknown parsers before fetching) + re-lookup (don't carry the class through `_FetchWork`). Don't stash the class on `_FetchWork`.
- `fetch_only` is checked at both lines 169 and 256 — also intentional (Phase 1 short-circuit skips FetchUrl lookup work; Phase 3 short-circuit skips parse-store). Both must stay.
- `session.commit()` at lines 286–287 is inside the loop. Per-URL commit releases the SQLite writer lock between URLs so concurrent PHP readers don't hit `SQLITE_BUSY` while the pipeline is running. Stays in the calling loop, not in `_parse_and_store`'s try-block — though that loop IS in `_parse_and_store` after extraction, so the commit travels with it.

#### 3b. `cli/calc_rating.py:32 calc_rating` (cc=22 → ≤7) — 5 tests; +2 pinned tests added before refactor

Refactor target is the 3-nested-loop + 3-way conditional structure (lines 43–158). The conditional collapses naturally:

- `_load_rating_for_gauge(session, gauge) -> tuple[list, list] | None` — lines 45–57. Returns `(feet_to_cfs, cfs_to_feet)` or `None`. Isolates the non-obvious `cfs_to_feet` reverse-sort.
- `_apply_rating_to_source(session, source_id, feet_to_cfs, cfs_to_feet, neg_flow_sources) -> tuple[bool, bool]` — lines 63–146 (the entire per-source body, including the `update_latest` calls at lines 141–146). Returns `(new_gauge, new_flow)`.

Inside `_apply_rating_to_source`, the current 3-way (`if not gauge_records` / `elif not flow_records` / `else` at lines 79, 91, 107) is **equivalent to two parallel "fill missing" calls**:

```
gauge_times = {rec.observed_at for rec in gauge_records}
flow_times = {rec.observed_at for rec in flow_records}
new_gauge = _fill_gauge_from_flow(session, source_id, flow_records, gauge_times, cfs_to_feet, neg_flow_sources)
new_flow = _fill_flow_from_gauge(session, source_id, gauge_records, flow_times, feet_to_cfs, neg_flow_sources)
```

— because "missing" is "not in the other set", and when the other set is empty (the `not gauge_records` / `not flow_records` branches), every record is missing. The pre-loop time-set snapshot at lines 108–109 is the invariant that lets this collapse work: newly-stored rows inside the loop don't observe each other.

**Pre-refactor tests added to `tests/test_cli/test_calc_rating.py` (in a separate pre-Phase-3 commit, not bundled with the refactor):**
1. **`test_both_exist_uses_pre_loop_time_sets`** — gauge at t1, flow at t2; after run, both columns have exactly {t1, t2}. Catches "newly-stored rows leak into the in-loop time set" — the specific regression the collapse risks.
2. **`test_out_of_range_value_yields_no_row`** — gauge value far outside rating table; assert no flow row created (`val is None` path through `interpolate_rating`).

**Risk flags:**
- `val > 0` guard at lines 96 and 129 is **only on flow output**, not gauge. Preserve in `_fill_flow_from_gauge` but not in `_fill_gauge_from_flow`.
- Per-gauge `try/except + rollback + commit` at lines 44, 154, 156–158 stays at the gauge-loop level — helpers raise upward.
- The per-source `update_latest(source_id, ...)` calls (lines 141–146) belong **inside** `_apply_rating_to_source` so the source-level side effects are co-located with the source-level computation. The gauge-level `update_latest_gauge(gauge.id, ...)` calls (lines 148–151) stay in the outer loop because they're per-gauge.

#### 3c. `cli/calculator.py:104 calculator` (cc=30 → ≤8) + `_safe_eval` / `_eval` → `# noqa: C901` — 8 tests in `test_cli/test_calculator.py`

Four logical blocks split into named helpers:

- `_topo_sort_calc_sources(calc_sources, source_to_gauge, gauge_id_to_name) -> list[Source]` — body covers ~ lines 127–183 (the topo-sort proper; the initial `calc_sources = list(...)` collection at line 110 stays in the caller). The nested `_get_deps` at ~ lines 132–153 becomes a private helper inside this function — or a module-level helper if you want it independently testable (the existing `test_circular_dependency_raises` would then pin it). Raises `ValueError` on cycle (current behavior).
- `_resolve_refs(session, time_expression, name_to_gauge_id) -> tuple[dict[str, float], list[datetime]] | None` — body covers ~ lines 209–256. Returning `None` replaces the **five** `break` statements (lines 218, 231, 238, 244, 253) + `skip` flag pattern; the caller's `if skip or not times: continue` at lines 258–259 becomes `if resolved is None: continue`. Each `logger.error/warning` stays inside the helper.
- `_substitute_placeholders(expression, values) -> tuple[str, dict[str, float]]` — lines 269–278. Pure string/dict transform; longest-first ordering rule becomes unit-testable directly. The existing test `test_substring_refs_do_not_collide` would pin this helper.
- `_store_calc_result(session, source, data_type, when, result, source_to_gauge, neg_flow_sources) -> None` — lines 286–303.

Resulting `calculator` body: `session = get_session() / try:` → gather sources + build lookup dicts (lines 109–125 stay inline) → `calc_sources = _topo_sort_calc_sources(...)` → per-source loop with `try:/_resolve_refs/None-skip/_substitute_placeholders/_safe_eval try/_store_calc_result/session.commit() / except`.

**`_safe_eval` and `_eval` get `# noqa: C901` markers** with a one-line comment explaining the AST-visitor-dispatch rationale (see *Decisions baked in*).

**Risk flags:**
- The per-source `try: ... except Exception: rollback; logger.exception` at lines 186, 308–310 wraps the entire body. **Helpers raise upward**; the outer loop catches and rolls back against `source.name`.
- `session.commit()` at line 306 stays in the calling loop, not in `_store_calc_result` — SQLite-writer-lock release pattern.
- `_get_deps`'s closure over `gauge_id_to_name` / `source_to_gauge` / `calc_gauge_names` becomes explicit args when the function is promoted to module scope.
- The `times` list (line 210) accumulates across refs and `min(times)` at line 262 picks the earliest. `_resolve_refs` must return the full list, not just the min — the caller uses the list for `times` but also passes the min as `when`. (Alternatively, `_resolve_refs` returns `(values, when)` directly and `times` never escapes the helper.)

**After Phase 3:** drop `"src/kayak/cli/*"` from `[tool.ruff.lint.per-file-ignores]`. The stanza retains only `"src/kayak/tracing/*" = ["C901"]`. Update the stanza comment to point at the tracing mypy override as the shared rationale.

**Verification gate (Phase 3):** Phase 2 gate plus the same single-run DB diff for each of `levels fetch`, `levels calc-rating`, `levels calculator` against a fresh snapshot. Same-code baseline run before each refactor so the post-refactor diff has a known-clean reference. (`$KAYAK_DB` set as in Phase 2.)

```bash
# Per subcommand:
cp "$KAYAK_DB" /tmp/p3-snap.db
sqlite3 /tmp/p3-snap.db ".dump observation latest_observation latest_gauge_observation" > /tmp/p3-snap.sql

# Same-code baseline:
DATABASE_URL=sqlite:////tmp/p3-snap.db levels <subcmd>
sqlite3 /tmp/p3-snap.db ".dump observation latest_observation latest_gauge_observation" > /tmp/p3-baseline.sql
diff /tmp/p3-snap.sql /tmp/p3-baseline.sql | wc -l   # records the no-op drift

# Refactor commit, then:
cp "$KAYAK_DB" /tmp/p3-after.db
DATABASE_URL=sqlite:////tmp/p3-after.db levels <subcmd>
sqlite3 /tmp/p3-after.db ".dump observation latest_observation latest_gauge_observation" > /tmp/p3-after.sql
diff /tmp/p3-snap.sql /tmp/p3-after.sql | wc -l      # should be ≈ baseline + (new obs in window)
```

## Risks

- **Hidden behavior change in helpers.** Extraction must preserve order-of-operations and side effects exactly. The biggest trap is mutable state passed across helpers (DB session, in-loop `skip` flags, pre-computed time sets). Each Phase-3 risk flag above is a specific known instance; treat them as a checklist when reviewing the diff.
- **SQLite writer-lock pattern lost.** Per-URL commits (fetch) and per-source commits (calculator/calc_rating) release the writer lock between iterations so concurrent PHP readers don't hit `SQLITE_BUSY`. Refactor must keep those commits where they are — inside the appropriate per-iteration scope, not hoisted to the outer function.
- **Test coverage is module-level, not function-level.** A passing `pytest` does not prove every code path in the refactored function is exercised. For Phase 3, the per-subcommand DB diff is the supplementary signal; for Phase 1/2, peer-review the diff against the original function side-by-side.
- **Plan-agent extraction sketches drift from current code.** Plan was drafted at `main = 2f39e15`. If `main` advances before a phase starts, re-run §Reproduce and adjust line refs before quoting them in commit messages.
- **`pytest`-pass-count drift between machines.** Local macOS skips `test_huc/test_assign.py` (missing `geopandas` extra); CI does not. Quote `pytest --collect-only -q` numbers (`collected / deselected / skipped`) in commit messages, not just `N passed`.
- **Phase 2/3 DB diff is noisy.** Live `kayak.db` accumulates observations between two consecutive subcommand runs by definition. The verification gate compares *delta to the same-code baseline*, not absolute byte equality.
- **`_get_deps` (calculator) is a closure over three dicts.** When promoting it (or its parent `_topo_sort_calc_sources`) to module scope, the three closure dicts (`gauge_id_to_name`, `source_to_gauge`, `calc_gauge_names`) become explicit parameters. Missing one in the signature change is a silent name-error trap at call time, not a static-analysis miss.
- **Phase 1's `_TAG_HANDLERS` table option for `nwrfc_xml` is more invasive than a helper extraction.** If the dispatch-table form makes the diff too large to review confidently in one pass, fall back to the helper-extraction form. Both reach the cc target; only the diff size differs.

## Out of scope

- ~~**`tracing/trace.py` (`trace_reach` cc=15, `find_huc4` cc=11).**~~ → **Closed 2026-05-12 in `b662118`** (see status banner at top). Original rationale: no tests exist for this module; its mypy override in `pyproject.toml:92–98` documents it as deferred standalone-script integration. Refactor when the module gets fully integrated (tests + type annotations + complexity in one PR), not as a one-off cc fix. The closure shipped tests + complexity; type annotations remain deferred (next bullet).
- **Type annotations for `tracing/trace.py`.** Still deferred — the `[tool.mypy.overrides]` carve-out for `kayak.tracing.*` stays. `b662118` shipped tests + complexity but left annotations for a follow-up.
- **Refactoring `_safe_eval` / `_eval` in `calculator.py`.** Marked `# noqa: C901` instead (see *Decisions baked in*).
- **Module splits.** `cli/calculator.py` (314 LOC), `cli/fetch.py` (403 LOC), `utils/http_client.py` (431 LOC) all stay as single files. The build.py-split precedent (2187 LOC monolith) does not apply here.
- **New integration tests** beyond the two pinned regressions for `calc_rating`. Existing coverage is the regression net.
- **The 11 functions in `web/build/` that are already cc≤10.** Phase 9 of the build.py split closed those out.
- **Any `cli/build.py` work.** That file is a 5-line shim; nothing left to refactor.
- **Adopting more ruff rules.** `C901` is the only complexity rule enabled here. Anything else (B008, PLR0912, PLR0913, etc.) is a separate decision.

## Reproduce

Read-only commands to verify current state before starting Phase 1.

```bash
# Authoritative cc>10 list (bypasses per-file-ignores so all hits surface).
# Cross-reference against the Target Shape table to confirm the same 15 hits.
ruff check src/ --select C901 --no-cache \
  --config 'lint.per-file-ignores={}' \
  --output-format=concise

# Per-function line lookups (should match `file:line` in the Target Shape table)
grep -nE "^def |^    def " \
  src/kayak/cli/fetch.py src/kayak/cli/calculator.py \
  src/kayak/cli/calc_rating.py src/kayak/cli/fetch_usgs_ogc.py \
  src/kayak/huc/assign.py src/kayak/tracing/trace.py \
  src/kayak/utils/http_client.py src/kayak/parsers/*.py

# Test counts per module (uses grep -cE "def test_" to catch both top-level
# and class-method tests). Expect the numbers quoted in §Constraints.
for f in tests/test_cli/test_fetch.py tests/test_cli/test_calculator.py \
         tests/test_cli/test_calc_rating.py tests/test_cli/test_fetch_usgs_ogc.py \
         tests/test_parsers/test_nwps.py tests/test_parsers/test_nwrfc_xml.py \
         tests/test_parsers/test_usace_cda.py tests/test_parsers/test_wa_gov.py \
         tests/test_utils/test_http_client.py tests/test_huc/test_assign.py; do
  if [ -e "$f" ]; then
    n=$(grep -cE "def test_" "$f" 2>/dev/null)
    printf "%-50s %s\n" "$f" "$n"
  fi
done

# Per-file-ignores stanza this plan shrinks
sed -n '/per-file-ignores/,/^\[/p' pyproject.toml

# Tracing mypy override referenced in §Decisions baked in (lines 92–98)
sed -n '92,98p' pyproject.toml

# DB snapshot path differs between dev (macOS) and prod (Debian):
#   dev:  ~/tpw/DB/kayak.db
#   prod: /home/pat/DB/kayak.db
# Substitute the right one in the §Phase 2/3 verification commands.
```

After each commit, the same `ruff` command with `lint.per-file-ignores={}` should drop the just-refactored functions from its output. Phase-1 commit removes 6 hits; Phase 2 removes 2; Phase 3a/3b/3c each remove 1; the two `# noqa: C901` markers in Phase 3c suppress 2 more (the `noqa` mechanism is orthogonal to `per-file-ignores`, so it applies under the override-bypass too).

End state:
- `ruff check src/` (production form, with `per-file-ignores` active): **0 hits**.
- `ruff check src/ --config 'lint.per-file-ignores={}'` (override-bypass form, for auditing): **2 hits** — both in `tracing/trace.py`, deferred per §Out of scope.
