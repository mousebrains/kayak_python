# Plan — Split `src/kayak/cli/build.py` into a `kayak.web.build` package

> **Cross-check:** plan drafted 2026-05-11 from macOS dev checkout (`/Users/pat/tpw/kayak/`) against a pulled snapshot of the production DB at `/Users/pat/tpw/DB/kayak.db`. A second Claude session on the live Debian system (DB at `/home/pat/DB/kayak.db`) should re-run the read-only commands in **§Reproduce** below and confirm the findings before any edits land.
>
> Dates are absolute. References are `file:line` against `main` at the time of writing.
>
> **Last verified against `main`:** commit `b7eb123` (2026-05-11). Confirmed: `src/kayak/cli/build.py` is 2187 lines / 50 functions; `levels build` accepts `--output-dir OUTPUT_DIR` (default `$OUTPUT_DIR` or `public_html/`); `ruff check --select C901` reports three offenders — `_get_row_data` (cc=16), `_collect_gauge_rows` (cc=17), and `_deploy_source_files` (cc=11); five tests under `tests/test_build_*.py` and `tests/test_cli/test_build.py` + `tests/test_cli/test_main.py` import from `kayak.cli.build`.

## Why

`src/kayak/cli/build.py` is the highest-LOC file in the repo (2187 lines, 50 functions, 54% test coverage) and houses every distinct concern in the static-HTML pipeline — HTML shell, per-state levels table, gauges page, sparkline SVGs, CSV/text exports, geojson, deploy orchestration. Three functions trip ruff C901 (currently suppressed): `_get_row_data` (cc=16), `_collect_gauge_rows` (cc=17), `_deploy_source_files` (cc=11). `_build_filter_bar` is 128 lines.

Goal: split by concern so each module is independently testable and tractable.

## Current shape

Top-level constants (lines 48–137, 224–234, 422, 505–519, 1293–1306) and 50 functions in seven discernible clusters:

| Cluster | Lines | Functions | Notes |
|---|---|---|---|
| Shell (nav/footer/page) | 999–1292 | `_editor_feature_on`, `_build_nav`, `_build_right_cluster`, `_build_footer_html`, `_build_letter_nav`, `_build_page`, `_build_placeholder_page`, `_build_map_page` | + `_og_meta`, `_load_css`, `_css_link_tag` (78–249) |
| Levels table | 250–331 + 505–871 | `_get_builder_columns`, `_get_row_data` (cc=16), `_levels_key`, `_filter_visible_rows`, `_format_cell_value`, `_row_filter_attrs`, `_build_html_table`, `_collect_filter_data`, `_build_filter_bar` (128 lines) | Two disjoint slices — sparklines (332–421) and exports (422–504) are interleaved between them. The levels constants block (`_TD_CLASS`, `_SECONDARY_FIELDS`, `_GAUGE_FIELDS`) lives at 505–519, just before `_levels_key`. **`_GAUGE_FIELDS` (line 519) is dead code** — defined but never referenced; drop on the levels move. Densest cluster overall. Has **zero** outgoing calls to other clusters. |
| Sparklines | 332–421 | `_select_sparkline_series`, `_sparkline_svg_from_records`, `_build_sparkline` | + `SPARKLINE_*` constants (48–63) |
| Exports (CSV/text) | 422–504 | `_csv_safe`, `_build_csv`, `_build_text` | + `_CSV_FORMULA_PREFIX`. **Calls levels** (`_get_row_data` at lines 451, 485) — not a true leaf despite the constants being self-contained. |
| GeoJSON | 872–998 | `_reach_geometry`, `_build_reaches_static`, `_build_reaches_state` | + `GEOJSON_*` (69–70). **Calls levels** (`_get_row_data` at line 972 inside `_build_reaches_state`) — not a true leaf. |
| Gauges page | 1293–1799 | 11 functions incl. `_collect_gauge_rows` (cc=17, 115 lines), `_build_gauges_table` (111 lines), `_build_gauges_filter_bar`, station-name parsers | Has its own constants block. **Calls into levels** (`_build_gauges_filter_bar` → `_build_filter_bar` at line 1738; `_gauge_status_from_reaches` → `_get_row_data` at line 1455), into shell (`_build_page` at line 1764), and into sparklines (`_write_gauges_page` → `_select_sparkline_series` at 1786, `_sparkline_svg_from_records` at 1788). |
| Deploy / CLI | 1800–2187 | `addArgs`, `build`, `_build_to_dir` (95 lines), `_build_and_write`, `_deploy_source_files` (cc=11), `_deploy_staging_to_live`, `_sweep_orphans`, `_emit_sitemap`, `_set_acls`, `_atomic_write` (defined at 100) | CLI entry point. `_atomic_write` is the only deploy-bucketed helper that lives outside the 1800-2187 range — it sits up front because levels/gauges/deploy all call it. |

## Target shape

```
src/kayak/web/build/
├── __init__.py        # empty
├── _shared.py         # BRAND_*, _STATE_ABBREVS, _ABBR_TO_STATE,
│                      # _NAV_STATES, DATA_*_THRESHOLD, _STATIC_DIR + JS path
│                      # constants, _atomic_write, _og_meta, _load_css,
│                      # _css_link_tag, _editor_feature_on
├── sparklines.py      # SPARKLINE_* + 3 fns
├── exports.py         # _CSV_FORMULA_PREFIX + _csv_safe, _build_csv, _build_text
├── geojson.py         # GEOJSON_* + _reach_geometry, _build_reaches_static,
│                      # _build_reaches_state
├── shell.py           # _build_nav, _build_right_cluster, _build_footer_html,
│                      # _build_letter_nav, _build_page, _build_placeholder_page,
│                      # _build_map_page
├── levels.py          # _TD_CLASS, _SECONDARY_FIELDS + the 9-function
│                      # levels table + filter bar cluster
│                      # (_GAUGE_FIELDS at line 519 is dead — drop, don't move)
├── gauges.py          # 11-function gauges-page cluster + its constants
└── deploy.py          # build(), addArgs(), _build_to_dir, _build_and_write,
                       # _deploy_source_files, _deploy_staging_to_live,
                       # _sweep_orphans, _emit_sitemap, _set_acls
```

Dependency graph (acyclic), five tiers:

```
_shared
  ↑
{sparklines, shell, levels}    three true leaves — only edges are to _shared
  ↑
{exports, geojson}             depend on _shared + levels (_get_row_data)
  ↑
gauges                         depends on _shared + sparklines + shell + levels
  ↑
deploy                         orchestrator — calls every cluster
```

Key edges to note:
- `_build_csv` (line 451) and `_build_text` (line 485) call `_get_row_data` (levels).
- `_build_reaches_state` (line 972) calls `_get_row_data` (levels).
- `_build_gauges_filter_bar` (line 1738) calls `_build_filter_bar` (levels).
- `_gauge_status_from_reaches` (line 1455) calls `_get_row_data` (levels).
- `_write_gauges_page` (1786, 1788) calls `_select_sparkline_series` and `_sparkline_svg_from_records` (sparklines).
- `_write_gauges_page` (line 1764) calls `_build_page` (shell).

Phase order follows the dep graph topologically: scaffolding → three leaves (sparklines, shell, levels) → mid-tier (exports, geojson) → gauges → deploy → refactor. Each new module imports its dependencies directly from their final homes — no transitional re-exports leak between new modules.

The legacy `cli/build.py` does still need short-lived re-imports at its own top (`from kayak.web.build.levels import _get_row_data` etc.) so that the un-moved functions remaining inside it can resolve their dependencies after each phase. That clutter is confined to the file being dismantled and dies entirely when Phase 8 replaces `cli/build.py` with the slim shim.

`src/kayak/cli/build.py` becomes a permanent 2-line shim:

```python
from kayak.web.build.deploy import addArgs, build  # noqa: F401
```

Keeps `kayak.cli.main`'s import sites unchanged.

## Migration phases

Nine commits in topological dep-graph order. Tests + ruff + mypy must stay green between phases. Test imports and `mock.patch` strings update at the phase that moves the underlying symbol — every commit is bisectable on its own and the test suite stays correct after each. `tests/test_cli/test_build.py`'s 13-symbol import block fans out across Phases 1–5 (1 + 2 + 4 + 3 + 3 = 13); none of it is deferred to Phase 8.

1. **Phase 1 — scaffolding + `_shared`.** Create `src/kayak/web/build/{__init__.py,_shared.py}` (`src/kayak/web/__init__.py` already exists). Move the truly shared constants and five small helpers (`_og_meta`, `_load_css`, `_css_link_tag`, `_atomic_write`, `_editor_feature_on`) into `_shared.py`. Re-import them in `cli/build.py` so all current call sites keep working. Each new module also gets its own `logger = logging.getLogger(__name__)`.
   - `tests/test_cli/test_build.py`: split the 13-symbol import block; move `_atomic_write` to a new `from kayak.web.build._shared import _atomic_write` line.
   - ⚠ When moving `_STATIC_DIR` (line 224) the path arithmetic must change. The current form `Path(__file__).resolve().parent.parent / "web" / "static"` assumes `__file__` is `src/kayak/cli/build.py`; from `src/kayak/web/build/_shared.py` it must become `Path(__file__).resolve().parent.parent / "static"` (drop the `"web"` segment, since `__file__.parent.parent` is now already inside `web/`). `_CSS_PATH`/`_JS_PATH`/`_FILTERS_JS_PATH` compose off `_STATIC_DIR` and inherit the fix transitively.
2. **Phase 2 — sparklines.** Move `SPARKLINE_*` constants + 3 functions into `web/build/sparklines.py`. Re-import in `cli/build.py` (the still-resident gauges-cluster code and deploy code both call into sparklines until those clusters move).
   - `tests/test_cli/test_build.py`: move `_build_sparkline` and `_select_sparkline_series` (2 symbols) to a `from kayak.web.build.sparklines import …` line.
3. **Phase 3 — shell.** Move nav / footer / page-shell helpers (`_build_nav`, `_build_right_cluster`, `_build_footer_html`, `_build_letter_nav`, `_build_page`, `_build_placeholder_page`, `_build_map_page`) into `web/build/shell.py`. Re-import in `cli/build.py`.
   - `tests/test_build_filters.py`: move `_build_page` to a `from kayak.web.build.shell import _build_page` line (1 of its 5 imports; the other 4 stay on `kayak.cli.build` via re-export until Phases 4 and 7).
   - `tests/test_cli/test_build.py`: move `_build_letter_nav`, `_build_map_page`, `_build_nav`, `_build_page` (4 symbols) to a `from kayak.web.build.shell import …` line.
4. **Phase 4 — levels.** Move the 9-function levels cluster + its constants block (`_TD_CLASS`, `_SECONDARY_FIELDS`). Drop `_GAUGE_FIELDS` (line 519, defined but unused — verified with `grep -n _GAUGE_FIELDS`: only the assignment line matches). After this phase, levels is a true leaf inside `kayak.web.build`; gauges (Phase 7) will import directly from it.
   - `tests/test_build_filters.py`: move `_build_filter_bar`, `_collect_filter_data`, `_row_filter_attrs` to a `from kayak.web.build.levels import …` line (3 of its 5; `_build_page` already moved in Phase 3; `_build_gauges_filter_bar` lands in Phase 7).
   - `tests/test_cli/test_build.py`: move `_build_html_table`, `_get_row_data`, `_levels_key` to a `from kayak.web.build.levels import …` line.
   - **`mock.patch` retargets — `_get_row_data` (15 of the 20 patches).** Tests inside `TestBuildHTMLTable` and the downstream classes that exercise `_build_html_table` patch `"kayak.cli.build._get_row_data"` at lines 490, 501, 511, 525, 544, 557, 567, 585, 610, 622, 636, 648, 661, 671, 682. After this phase, `_build_html_table` lives in `kayak.web.build.levels` and looks up `_get_row_data` in its own module namespace — retarget all 15 to `"kayak.web.build.levels._get_row_data"`. The other 5 patches (lines 428, 441, 453, 462, 473) test `_build_csv`/`_build_text`, which still live in `cli/build.py` at this phase — they stay on `"kayak.cli.build._get_row_data"` until Phase 5 moves exports.
   - **Vestigial `_build_sparkline` patches — delete, don't retarget.** Lines 491, 502, 512, 526, 545, 558, 568, 586, 611, 623, 637, 649, 662, 672, 683 patch `"kayak.cli.build._build_sparkline"` defensively alongside `_get_row_data`, but `_build_html_table` (line 615) does not call `_build_sparkline`. The only production call site is `_build_and_write` (deploy, line 2182), which the test path never reaches. Confirmed by `grep -n "_build_sparkline(" src/kayak/cli/build.py` → only matches are the `def` at 405 and the deploy call at 2182. Strip these 15 lines as part of this phase.
5. **Phase 5 — exports.** Move `_CSV_FORMULA_PREFIX` + 3 functions (`_csv_safe`, `_build_csv`, `_build_text`) into `web/build/exports.py`. `exports.py` imports `_get_row_data` directly from `kayak.web.build.levels` (Phase 4 already moved it) — no transitional re-import via `cli/build.py`.
   - `tests/test_cli/test_build.py`: move `_build_csv`, `_build_text`, `_csv_safe` to a `from kayak.web.build.exports import …` line.
   - **`mock.patch` retargets — `_get_row_data` (remaining 5 patches).** Tests inside `TestBuildCSV` / `TestBuildText` patch `"kayak.cli.build._get_row_data"` at lines 428, 441, 453, 462, 473. After this phase, `_build_csv` and `_build_text` live in `kayak.web.build.exports` and look up `_get_row_data` in that module's namespace — retarget all five to `"kayak.web.build.exports._get_row_data"`.
6. **Phase 6 — geojson.** Move `GEOJSON_*` + 3 functions (`_reach_geometry`, `_build_reaches_static`, `_build_reaches_state`) into `web/build/geojson.py`. `geojson.py` imports `_get_row_data` directly from `kayak.web.build.levels`.
   - `tests/test_build_geojson_split.py`: 1 import line, 2 symbols → `kayak.web.build.geojson`. No mock.patch concerns (this test file uses no patches).
7. **Phase 7 — gauges.** Move the 11-function gauges cluster + its 4 constants. `gauges.py` imports `_build_filter_bar` and `_get_row_data` from `kayak.web.build.levels`, `_build_page` from `kayak.web.build.shell`, and `_select_sparkline_series` + `_sparkline_svg_from_records` from `kayak.web.build.sparklines`.
   - `tests/test_build_filters.py`: move `_build_gauges_filter_bar` to a `from kayak.web.build.gauges import _build_gauges_filter_bar` line (the last remaining import in that file). All five of its imports are now correct.
   - **Out-of-tree consumer.** `scripts/seed_gauge_display.py:39` does `from kayak.cli.build import _parse_station_mixed, _parse_station_uppercase` — both functions are moving to `gauges.py` in this phase. Update that script's import to `from kayak.web.build.gauges import _parse_station_mixed, _parse_station_uppercase` at the same time, or the script breaks when the slim shim lands in Phase 8. (This is the only consumer of `kayak.cli.build` symbols outside `src/` and `tests/` — verified with `grep -RnE "from kayak\.cli\.build|kayak\.cli\.build" --include="*.py" src/ tests/ scripts/`.)
8. **Phase 8 — deploy + slim shim.** Move `build`, `addArgs`, `_build_to_dir`, `_build_and_write`, `_deploy_*`, `_emit_sitemap`, `_set_acls` into `web/build/deploy.py`. `deploy.py` imports from every other module in the new package. Replace `src/kayak/cli/build.py` with the 2-line re-export shim. No intra-package import repointing is needed — topological order in phases 1–7 already had every new module import from its final home.
   - `tests/test_build_deploy.py`: update the two named imports (`_deploy_staging_to_live`, `_sweep_orphans`) to `kayak.web.build.deploy`. Rewrite the alias on line 15 from `import kayak.cli.build as build_mod` to `import kayak.web.build.deploy as build_mod` so the `build_mod.shutil.copy2` lookup at line 216 and the `mp.setattr(build_mod.shutil, "copy2", ...)` at line 226 hit the deploy module's namespace (where `shutil` is actually imported). The slim `cli/build.py` shim only imports `addArgs` and `build`, so `shutil` is no longer an attribute there.
9. **Phase 9 — refactor the cc>10 offenders inside their new homes.** Three functions:
   - `_get_row_data` (cc=16) → `levels.py`: extract per-cell formatters / per-data-type branches into helpers.
   - `_collect_gauge_rows` (cc=17) → `gauges.py`: extract status-classification and metadata-merge into helpers.
   - `_deploy_source_files` (cc=11) → `deploy.py`: extract the five sequential copy blocks (static assets w/ `sw.js` routing; PHP files at root; PHP includes; CSS for PHP inlining; config files from `public_html/`) into three helpers — `_deploy_static_assets` (block 1), `_deploy_php_files` (blocks 2–4, all PHP-layer setup), `_deploy_config_files` (block 5). Lowest-risk of the three — pure file-copy refactor with no data-shape change.

   This is the only phase that should change behavior — and only at the function-extraction level, not the HTML output level. Enable `C901` in ruff (`pyproject.toml`).

Phases 1–8 are pure code motion: zero HTML output change, zero behavior change. Phase 9 is the real refactor.

## Verification gate (every phase)

Run before and after each phase. Generated HTML must be byte-identical for phases 1–8, *modulo two time-driven exceptions documented below*.

```bash
# 1. Lint + format + types stay clean
ruff check src/ tests/
ruff format --check src/ tests/   # CI runs this too — `make lint` does not
mypy src/

# 2. Tests still pass
pytest -x

# 3. Generated HTML is "byte-identical except for embedded build time"
rm -rf /tmp/build-before /tmp/build-after
levels build --output-dir /tmp/build-before    # before the phase's edit
# ... apply the phase ...
levels build --output-dir /tmp/build-after
diff -r -I 'now_iso\|now_display\|<time datetime=' /tmp/build-before /tmp/build-after
# must be empty for phases 1–8 (after the -I filter)
```

Why the filter is required: `_build_page` (line 1104-1106) embeds
`datetime.now(UTC)` as `now_iso` and `now_display` into every generated
page, and Phase 5+ moves that function unchanged into `shell.py`.
Without the filter the back-to-back builds would diff on any minute
boundary crossed.

Secondarily, three call sites classify rows as stale/expired or as
"sparkline-current" based on `datetime.now(UTC) - obs_time` — a row
whose age crosses one of these boundaries between the two runs will
reclassify even though no code changed:

- `_get_row_data` (line 319) — `DATA_STALE_THRESHOLD` (48h) / `DATA_EXPIRY_THRESHOLD` (7d)
- `_collect_gauge_rows` (line 1543) — same two thresholds
- `_select_sparkline_series` (lines 343–344) — `SPARKLINE_OBSERVATION_WINDOW` (48h) / `SPARKLINE_CURRENT_WINDOW` (6h)

The third site is easy to miss because the sparkline series-selection
determines *which observations get rendered* — a flow series that drops
out of the 6h "current" window between runs will fall back to gauge-height,
and the embedded `<svg>` differs. To eliminate every source of drift,
snapshot the live DB before Phase 1 and point `DATABASE_URL` at the
snapshot for every gated build — or run the gate against a freshly-
`pytest`-built fixture DB where the staleness boundaries are far from now.

For Phase 9 only, additional whitespace/formatting diff is allowed if
it's an obvious consequence of the extracted helpers (e.g. an
indentation change). If real content differs, the refactor is broken
— revert and rework.

## Risks

- **Hidden cross-module state.** Three top-of-file constants (`_LEVELS_JS_VERSION`, `_FILTERS_JS_VERSION`, `_MAP_JS_VERSION`) are evaluated at import time via `stat().st_mtime`, and a fourth (`_LEVELS_JS`) is a derived f-string consuming `_LEVELS_JS_VERSION`. All four must live in one place (`_shared.py`) and be imported, not re-evaluated, to avoid duplicate stat calls and to keep the cache-busted URL stable across modules.
- **`_STATIC_DIR` path arithmetic.** Documented inline in Phase 1, but worth restating: `Path(__file__).resolve().parent.parent / "web" / "static"` only works from `src/kayak/cli/build.py`. After the move to `src/kayak/web/build/_shared.py`, the `/ "web"` segment must be dropped.
- **In-src consumers of `kayak.cli.build`.** `src/kayak/cli/main.py:8,44` imports `build` and calls `build.addArgs`. `src/kayak/cli/pipeline.py:18,66` imports `build` and dispatches `build.build`. The permanent shim re-exports both `addArgs` and `build`, so neither is at risk.
- **Out-of-tree consumer.** `scripts/seed_gauge_display.py:39` imports `_parse_station_mixed` and `_parse_station_uppercase` from `kayak.cli.build`; both move to `gauges.py` in Phase 7. Phase 7 updates the script's import; missing this would not be caught by `pytest` (no test runs that script) — only by an actual invocation.
- **`tests/test_cli/test_main.py:48`** does `from kayak.cli import build` — the permanent shim preserves this.
- **`tests/test_cli/test_build.py`'s 13-symbol import block.** Distributed across Phases 1–5 alongside the symbol moves (1 + 2 + 4 + 3 + 3 = 13). Each phase modifies one `from kayak.web.build.<module> import …` line; the test suite stays green after each phase. The block-style import statement at line 6 is replaced with five separate single-line imports over the course of those phases.
- **PHP side reads no Python modules**, so the PHP layer is unaffected.
- **Phase 9 (cc>10 refactor)** is the only phase that risks output diff. Done last so it can be reverted in isolation.

## Out of scope

- The `_build_filter_bar` 128-line function. It's complex but linear (build a `<select>` per filter type). After Phase 7 it can be split, but that's a follow-up — not part of this plan.
- PHP file lengths (`php/reach.php` 649, `php/includes/svg_plot.php` 503, `php/description.php` 495). Same anti-pattern but a separate problem.

## Reproduce

Read-only commands a second session can run to verify the findings above.

```bash
# Set DB path (parameterized for dev vs prod)
DB="${DB:-/home/pat/DB/kayak.db}"

# Confirm file size + function count
wc -l src/kayak/cli/build.py
grep -cE "^def |^class " src/kayak/cli/build.py

# Confirm cluster boundaries (function line numbers)
grep -nE "^def |^class " src/kayak/cli/build.py

# Confirm C901 offenders
ruff check --select C901 --no-cache src/kayak/cli/build.py

# Confirm test imports (which tests will need import updates per phase)
grep -RnE "from kayak.cli.build import|from kayak.cli import build" tests/

# Confirm CLI wiring entry point (the shim re-exports addArgs and build)
grep -nE "^\s*build\s*,|^\s*build\s*$|build\.(addArgs|build)\b" src/kayak/cli/main.py
grep -nE "build\.(addArgs|build)\b|from kayak\.cli import.*build" src/kayak/cli/pipeline.py

# Confirm out-of-tree consumer (scripts/seed_gauge_display.py)
grep -RnE "from kayak\.cli\.build|kayak\.cli\.build" --include="*.py" \
  src/ tests/ scripts/

# Confirm the build CLI exposes --output-dir for the golden-file gate
levels build --help
```
