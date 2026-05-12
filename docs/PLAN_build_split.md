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
| Levels table | 250–871 | `_get_builder_columns`, `_get_row_data` (cc=16), `_levels_key`, `_filter_visible_rows`, `_format_cell_value`, `_row_filter_attrs`, `_build_html_table`, `_collect_filter_data`, `_build_filter_bar` (128 lines) | Densest cluster |
| Sparklines | 332–421 | `_select_sparkline_series`, `_sparkline_svg_from_records`, `_build_sparkline` | + `SPARKLINE_*` constants (48–63) |
| Exports (CSV/text) | 422–521 | `_csv_safe`, `_build_csv`, `_build_text` | + `_CSV_FORMULA_PREFIX` |
| GeoJSON | 872–998 | `_reach_geometry`, `_build_reaches_static`, `_build_reaches_state` | + `GEOJSON_*` (69–70) |
| Gauges page | 1293–1799 | 11 functions incl. `_collect_gauge_rows` (cc=17, 115 lines), `_build_gauges_table` (111 lines), station-name parsers | Has its own constants block |
| Deploy / CLI | 1800–2187 | `addArgs`, `build`, `_build_to_dir` (95 lines), `_build_and_write`, `_deploy_source_files` (cc=11), `_deploy_staging_to_live`, `_sweep_orphans`, `_emit_sitemap`, `_set_acls`, `_atomic_write` | CLI entry point |

## Target shape

```
src/kayak/web/build/
├── __init__.py        # empty
├── _shared.py         # BRAND_*, PRIMARY_STATE, _STATE_ABBREVS, _ABBR_TO_STATE,
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
├── levels.py          # _TD_CLASS, _SECONDARY_FIELDS, _GAUGE_FIELDS + the
│                      # 9-function levels table + filter bar cluster
├── gauges.py          # 11-function gauges-page cluster + its constants
└── deploy.py          # build(), addArgs(), _build_to_dir, _build_and_write,
                       # _deploy_source_files, _deploy_staging_to_live,
                       # _sweep_orphans, _emit_sitemap, _set_acls
```

Dependency graph (acyclic):

```
_shared ← {sparklines, exports, geojson, shell}
        ← {levels, gauges}       (consume shell + sparklines/exports/geojson)
        ← deploy                 (orchestrator)
```

`src/kayak/cli/build.py` becomes a permanent 2-line shim:

```python
from kayak.web.build.deploy import addArgs, build  # noqa: F401
```

Keeps `kayak.cli.main`'s import sites unchanged.

## Migration phases

Nine commits. Tests + ruff + mypy must stay green between phases. Sparkline / CSV / text / geojson modules go first because they are leaves with few internal callers, so the blast radius of each move is small.

1. **Phase 1 — scaffolding.** Create `src/kayak/web/build/{__init__.py,_shared.py}`. Move the truly shared constants and three head-shell helpers (`_og_meta`, `_load_css`, `_css_link_tag`, `_atomic_write`, `_editor_feature_on`) into `_shared.py`. Re-import them in `cli/build.py` so all current call sites keep working. Tests untouched.
2. **Phase 2 — sparklines.** Move `SPARKLINE_*` constants + 3 functions into `web/build/sparklines.py`. Re-import in `cli/build.py`.
3. **Phase 3 — exports (CSV + text).** Same pattern.
4. **Phase 4 — geojson.** Same pattern. Update `tests/test_build_geojson_split.py` imports to point at `kayak.web.build.geojson`. (3 test functions, 1 import.)
5. **Phase 5 — shell.** Move nav/footer/page-shell helpers. No test imports to update.
6. **Phase 6 — gauges page.** Move the 11-function gauges cluster + its 4 constants. The bulk of the gauges code is self-contained; the only callers in the rest of `build.py` are `_build_to_dir`/`_build_and_write` which go to `deploy.py` later.
7. **Phase 7 — levels table.** Move the 9-function levels cluster. Update `tests/test_build_filters.py` imports (5 symbols) and `tests/test_cli/test_build.py`.
8. **Phase 8 — deploy.** Move `build`, `addArgs`, `_build_to_dir`, `_build_and_write`, `_deploy_*`, `_emit_sitemap`, `_set_acls`. Update `tests/test_build_deploy.py` imports (2 symbols + module alias). Replace `src/kayak/cli/build.py` with the 2-line re-export shim.
9. **Phase 9 — refactor the cc>10 offenders inside their new homes.** Three functions:
   - `_get_row_data` (cc=16) → `levels.py`: extract per-cell formatters / per-data-type branches into helpers.
   - `_collect_gauge_rows` (cc=17) → `gauges.py`: extract status-classification and metadata-merge into helpers.
   - `_deploy_source_files` (cc=11) → `deploy.py`: extract the four sequential copy blocks into `_deploy_static_assets`, `_deploy_php_files`, `_deploy_config_files`. Lowest-risk of the three — pure file-copy refactor with no data-shape change.

   This is the only phase that should change behavior — and only at the function-extraction level, not the HTML output level. Enable `C901` in ruff (`pyproject.toml`).

Phases 1–8 are pure code motion: zero HTML output change, zero behavior change. Phase 9 is the real refactor.

## Verification gate (every phase)

Run before and after each phase. Generated HTML must be byte-identical for phases 1–8.

```bash
# 1. Lint + types stay clean
ruff check src/ tests/
mypy src/

# 2. Tests still pass
pytest -x

# 3. Generated HTML is byte-identical (golden-file gate)
rm -rf /tmp/build-before /tmp/build-after
levels build --output-dir /tmp/build-before    # before the phase's edit
# ... apply the phase ...
levels build --output-dir /tmp/build-after
diff -r /tmp/build-before /tmp/build-after     # must be empty for phases 1–8
```

For Phase 9 only, the HTML diff is allowed to be non-empty *if* the diff is whitespace/formatting only and an obvious consequence of the extracted helpers (e.g. an indentation change). If real content differs, the refactor is broken — revert and rework.

## Risks

- **Hidden cross-module state.** Five top-of-file constants (`_LEVELS_JS_VERSION`, `_FILTERS_JS_VERSION`, `_MAP_JS_VERSION`, `_LEVELS_JS`) are evaluated at import time via `stat().st_mtime`. They must stay in one place (probably `_shared.py`) and be imported, not re-evaluated, to avoid duplicate stat calls.
- **`tests/test_cli/test_main.py:48`** does `from kayak.cli import build` — the permanent shim preserves this.
- **PHP side reads no Python modules**, so the PHP layer is unaffected.
- **Phase 9 (cc>10 refactor)** is the only phase that risks output diff. Done last so it can be reverted in isolation.

## Out of scope

- The `_build_filter_bar` 128-line function. It's complex but linear (build a `<select>` per filter type). After Phase 7 it can be split, but that's a follow-up — not part of this plan.
- PHP file lengths (`reach.php` 649, `svg_plot.php` 503, `description.php` 495). Same anti-pattern but a separate problem.

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

# Confirm CLI wiring entry point (whether the shim is needed)
grep -n "from kayak.cli import\|cli\\.build" src/kayak/cli/main.py

# Confirm the build CLI exposes --output-dir for the golden-file gate
levels build --help
```
