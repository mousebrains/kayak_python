# Plan — Assign HUC10/HUC12 watershed codes to every reach

> **Cross-check:** Plan drafted 2026-04-20 from macOS dev checkout (`/Users/pat/tpw/kayak/`) against `/Users/pat/tpw/DB/kayak.db`. A second Claude session on live Debian (`/home/pat/DB/kayak.db`, `/home/pat/kayak/Trace-cache/`) should re-run **§Reproduce** below before any edit lands.
>
> Dates absolute. References are `file:line` against `main` at draft time.
>
> Cross-cutting feedback: this plan **only** delivers the HUC values. Hierarchical-filter UI design is sketched in §6 but is intentionally a follow-up so HUC values exist as a stable foundation first.
>
> **Last verified against `main`:** commit `cc6727d` (2026-04-20). Post-pull check: `reach.huc` and `gauge.huc` columns still exist (now at `src/kayak/db/models.py:139, :449`); `Trace-cache/NHD/hr/` still holds 19 HUC4 GDB ZIPs; `scripts/extract_trace_data.sh` still exists; **`scripts/trace_reach.py` is now a 0-line shim — heavy logic moved to `src/kayak/cli/trace_reach.py` + `src/kayak/tracing/trace.py` (T3-27)**. This pattern is the model for the new `levels assign-huc` subcommand below. The DB layer was also split (T3-20) into per-entity modules — new helpers go into `src/kayak/db/reaches.py`, not `data_db.py`.

## 1. Goal

Populate every reach with a Hydrologic Unit Code (HUC) so the basin/drainage filter on the levels tables can be:

- **Coarser** when the user picks a high-level basin: e.g. *Willamette* → all reaches in HUC4 = `1709`.
- **Finer** when the user picks a sub-basin: e.g. *Clackamas* → all reaches in HUC8 = `17090011`.
- **Hierarchical:** finer selections are subsets of coarser ones, automatically — no per-pair mapping table to maintain.

## 2. Why HUC12 (and how that handles HUC10/HUC8/HUC4 for free)

USGS HUCs are nested codes where each more-specific level is the parent's code with two more digits appended:

```
HUC2 = 17                Pacific Northwest
HUC4 = 17 09             Willamette
HUC6 = 17 09 00          Willamette (still)
HUC8 = 17 09 00 11       Clackamas
HUC10 = 17 09 00 11 04   Lower Clackamas River
HUC12 = 17 09 00 11 04 03  Eagle Creek-Clackamas River
```

If we store **HUC12** on each reach, every coarser level is a `SUBSTR(huc12, 1, N)`:

```sql
-- All reaches in the Clackamas (HUC8 = 17090011)
SELECT * FROM reach WHERE substr(huc12, 1, 8) = '17090011';

-- All reaches in the Willamette (HUC4 = 1709)
SELECT * FROM reach WHERE substr(huc12, 1, 4) = '1709';
```

The cost of storing HUC12 vs. HUC10 is negligible (12 chars vs 10), and HUC12 is the most granular publicly maintained level — picking it forecloses no future use cases. Going the other way (HUC10 → HUC12) would require re-running the assignment.

**Decision: store HUC12.**

## 3. Current state of `reach.huc`

The column **already exists** (`src/kayak/db/models.py:449`, also on `gauge` at `:139`):

```python
huc: Mapped[str | None] = mapped_column(Text)
```

…but is sparsely populated:

| Status | Count |
|---|---|
| `huc` is NULL or empty | 371 / 425 |
| `huc` has a value (all 4 chars / HUC4) | 54 / 425 |

The 54 populated values are HUC4 codes (`1710` × 34, `1801` × 14, `1709` × 6). No code anywhere in the repo reads the column — `grep -ri "huc"` returns only the two model declarations. So we can re-populate freely; nothing depends on the current values.

**Plan: keep the column name `huc`, redefine its semantics as HUC12.** The 54 existing HUC4 values become recoverable as `substr(huc12, 1, 4)` after backfill. No migration of dependent code is needed because there is none.

## 4. Data source: existing local cache

`Trace-cache/NHD/hr/` holds 19 NHDPlus HR HUC4 GDB ZIPs (covering HUC4s **1601–1604, 1701–1712, 1801–1803** — Pacific Northwest, Great Basin, California). All reach put-in coordinates fall within `lat 41.19–46.93 N, lon -124.28 to -113.99 W` — fully covered by these 19 GDBs.

Each GDB contains the WBD layers we need:

| Layer | Polys per HUC4 (1709 example) | Fields used |
|---|---|---|
| `WBDHU8` | 12 | `HUC8`, `Name`, `States` |
| `WBDHU10` | 72 | `HUC10`, `Name`, `States`, `HUType` |
| `WBDHU12` | 382 | `HUC12`, `Name`, `States`, `HUType`, `ToHUC` |

Across all 19 HUC4s, expect on the order of **~7000 HUC12** polygons total — small enough to load entirely into a single GPKG and rtree-index.

**No new downloads required.**

## 5. Assignment algorithm

### 5.1 One-time pre-extract: WBD polygons → single GPKG

Add `scripts/extract_wbd.sh` (sibling to `scripts/extract_trace_data.sh`):

```sh
#!/usr/bin/env bash
set -euo pipefail
# Extract WBDHU8 / WBDHU10 / WBDHU12 from every HUC4 GDB ZIP into a single
# Trace-cache/wbd.gpkg with three layers. Idempotent.
SRC=Trace-cache/NHD/hr
OUT=Trace-cache/wbd.gpkg
[[ -f "$OUT" ]] && { echo "$OUT exists; delete to re-extract"; exit 0; }
for zip in "$SRC"/NHDPLUS_H_*_HU4_GDB.zip; do
  for layer in WBDHU8 WBDHU10 WBDHU12; do
    ogr2ogr -f GPKG -update -append -nln "$layer" \
      "$OUT" "/vsizip/$zip" "$layer"
  done
done
ogrinfo -so "$OUT"   # report
```

Output: `Trace-cache/wbd.gpkg` (~50–100 MB) with three layers, each rtree-indexed.

### 5.2 Backfill: `levels assign-huc` subcommand (mirrors the new T3-27 trace pattern)

Following the architecture introduced for `levels trace` in commit `c9e341a`:

- **Thin CLI wrapper** at `src/kayak/cli/assign_huc.py` (~30 lines) — registers the subcommand, parses args, defers the GDAL import into the entry function so `levels --help` stays fast on systems without `osgeo` installed (mirrors `src/kayak/cli/trace_reach.py:1-15`).
- **Heavy logic** at `src/kayak/huc/assign.py` (~120 lines) — does the rtree build and the per-reach point-in-polygon assignment. Follows the `kayak/tracing/` package layout introduced in T3-27.
- **DB helpers** in `src/kayak/db/reaches.py` (the post-T3-20 home for reach queries). Add:
  - `iter_reaches_with_putin(session) -> Iterable[Reach]`
  - `set_reach_huc(session, reach_id: int, huc12: str) -> None`
  - `get_reach_huc_counts(session) -> dict[str, int]` (for the report)

Register in `src/kayak/cli/main.py` alongside `trace_reach`:
```python
from kayak.cli import assign_huc, ...
# ... in addArgs loop:
assign_huc.addArgs(subparsers)
```

CLI usage:
```sh
levels assign-huc                    # all reaches, in-place
levels assign-huc --dry-run          # report only
levels assign-huc --reach-id 3930    # single reach
```

Flow:
1. Load `Trace-cache/wbd.gpkg::WBDHU12` (382 × 19 ≈ ~7000 polygons) into memory with an rtree (Fiona + Shapely + rtree, or geopandas with `.sindex`).
2. For each `reach` with `latitude_start IS NOT NULL AND longitude_start IS NOT NULL`:
   - Build a Shapely `Point(lon, lat)`.
   - Query rtree for candidate polygons.
   - Find the one whose `.contains(pt)` is true.
   - If found, `set_reach_huc(session, reach.id, huc12)`.
3. Log counts: `assigned`, `unchanged`, `outside_coverage`, `no_coords`.

Performance: ~10 ms per reach with rtree → ~5 s total for 414 reaches.

The same module is invoked by the daily systemd timer (§7) — no separate script path.

### 5.3 New `huc` lookup table for human-readable filter labels

Add to `src/kayak/db/models.py`:

```python
class HucName(Base):
    __tablename__ = "huc_name"
    code: Mapped[str] = mapped_column(String(12), primary_key=True)  # HUC8/10/12
    level: Mapped[int] = mapped_column()                              # 8, 10, or 12
    name: Mapped[str] = mapped_column(Text)                           # WBD Name
    states: Mapped[str | None] = mapped_column(Text)                  # WBD States csv
```

Populated by the same backfill script (or a sibling `import_huc_names.py`). Rows: ~7000 HUC12 + ~1500 HUC10 + ~250 HUC8 ≈ 8750 rows total. Negligible.

This table lets PHP render filter labels:

```sql
SELECT code, name FROM huc_name WHERE level=8 AND code IN
  (SELECT DISTINCT substr(huc, 1, 8) FROM reach WHERE huc IS NOT NULL)
ORDER BY name;
```

…without ever touching the GPKG at request time.

### 5.4 Coordinate selection: which point per reach?

Three viable choices, chosen by simplicity:

| Choice | Pro | Con |
|---|---|---|
| **(a) Put-in only** ✅ | simplest; "where the run starts" is intuitive | long reaches that cross HUC boundaries get attributed to put-in side only |
| (b) Take-out only | symmetric to (a) | same con |
| (c) Whole `geom` line — assign to longest-overlap HUC | most accurate for long reaches | needs polygon-line intersection; ~10× slower |
| (d) Multi-HUC: store all HUCs the reach passes through (JSON / new table) | richest filter UX | schema cost; later UX work to use it |

**Pick (a) for v1.** Reach put-ins are stable and intuitive. (c) and (d) are noted in §10 as future work — switch is non-breaking because the column stays HUC12 either way.

## 6. Filter-UI changes (sketched; can land in a follow-up)

Current contract (`src/kayak/web/static/filters.js:11-12`): table rows expose `data-state`, `data-basin`, `data-status`, `data-tier`. The basin filter is a flat pill list driven by `reach.basin`.

Minimal hierarchical-filter wiring:

1. `levels build` (`src/kayak/cli/build.py:496-505`) emits a new attribute `data-huc12="170900110403"` on each `<tr>` (alongside or replacing `data-basin`).
2. `filters.js` adds a new group key `huc8` whose pills are HUC8 names (e.g. *Clackamas*, *Sandy*, *Middle Willamette*, …) and whose match function uses **prefix match** instead of equality:
   ```js
   if (g.key === 'huc8') {
     // values is [data-huc12]; pill values are HUC8 codes
     for (const p of g.pills) if (p.input.checked && row.dataset.huc12.startsWith(p.input.value)) {
       anyChecked = true; break;
     }
   } else { /* existing equality */ }
   ```
3. Group HUC8 pills under collapsible HUC4 sections (`<details>` already used by the filter bar). User picks individual HUC8s; selecting "All" inside a HUC4 section is equivalent to "all reaches in that HUC4" thanks to prefix matching.
4. Optionally render labels as "HUC8 name (HUC4 name)" so a user who knows only "Willamette" sees the parent context next to "Clackamas".
5. Hash-state key: add `h` to `HASH_KEYS` (`filters.js:24`) so URLs round-trip the HUC8 selection.
6. Keep `data-basin` for one release as a back-compat fallback; remove in a later cleanup.

The Python build side reads HUC8 names from the new `huc_name` table; no GIS work at build time.

**This part is intentionally separable.** Once HUCs are populated, wiring up the UI is a small, low-risk change driven by visible data; landing the data first prevents the UI work from blocking.

## 7. Keeping HUCs fresh

| Trigger | Approach |
|---|---|
| New reach added via `php/edit.php` | Insert leaves `huc=NULL`. A daily systemd timer running `levels assign-huc` (or `python scripts/assign_huc.py`) picks it up. Acceptable lag: 24h. |
| Reach put-in coordinate edited | Same daily job — re-assigns by id when coords change. The script always recomputes (idempotent). |
| WBD released a new version | Manual: re-run `extract_wbd.sh` (after deleting `wbd.gpkg`), then `assign_huc.py` to refresh. WBD updates are infrequent (~yearly). |
| Reach moves across a HUC boundary due to WBD revision | Caught by next run of the script. |

A `levels` subcommand wrapper (`src/kayak/cli/assign_huc.py`) is a thin shim around the script so the systemd timer matches existing kayak-pipeline patterns.

**Why not assign at insert time in PHP?** PHP has no spatial libs in the prod stack, and shelling to Python from `edit.php` adds a fragile dependency. Daily reassignment is dramatically simpler and never blocks an edit.

## 8. Edge cases & decisions

| Case | Decision |
|---|---|
| Put-in inside CONUS land but on a HUC12 boundary | Shapely contains → first-match polygon wins (deterministic per WBD layer order). Acceptable. |
| Put-in over saltwater (estuary mouth) | May fall outside any HUC12. `huc` stays NULL. Filter falls back to "(none)" pill (current convention for missing basin). |
| Reach in BC, Yukon, or Alaska | Outside cached HUC4 coverage → `huc` NULL. None in current DB (max lat 46.93). |
| Long reach (e.g. 50 mi) crossing HUC8 boundaries | Attributed to put-in HUC12 only. Note as known limitation; (c) or (d) above is the upgrade path. |
| Reach with `latitude_start` but no `longitude_start` (or vice versa) | Skip — log as `no_coords`. |
| `geom` present but no put-in lat/lon | Out of scope for v1; could later parse first vertex of `geom`. |
| Existing 54 reaches with HUC4 in the column | Overwritten with HUC12 from coordinates. The HUC4 is preserved as `substr(huc, 1, 4)`. |
| Reach moved to `no_show=1` | Still gets a HUC; the filter-bar "(hidden)" toggle handles visibility separately. |
| `gauge.huc` (also exists at `models.py:139`) | Out of scope for this plan — gauges aren't directly filtered. Note for follow-up. |

## 9. Test reaches (live DB)

| Reach | Expected HUC8 | HUC8 Name |
|---|---|---|
| Any Clackamas reach (basin='Clackamas') | `17090011` | Clackamas |
| Any Sandy reach (basin='Sandy', id=…) | `17080001` | Lower Columbia-Sandy |
| Reach 3930 (North Santiam @ Niagara) | `17090005` | North Santiam |
| Reach 4988 (Willamette Albany) | `17090007` | Middle Willamette |
| Reach 791 (Chetco @ 14400000) | `17100311` | Chetco |
| Any Owyhee reach | `17050110` | Lower Owyhee (or similar 1705xxxx) |
| Any Snake reach in NV | `1705xxxx` or `1604xxxx` depending on segment |

After backfill, eyeball:

```sql
SELECT r.id, r.name, r.basin, r.huc, n.name AS huc8_name
FROM reach r
LEFT JOIN huc_name n ON n.code = substr(r.huc, 1, 8)
WHERE r.huc IS NOT NULL
ORDER BY r.basin, r.huc
LIMIT 30;
```

The free-text `basin` should align with the HUC8 name in most cases. Mismatches are useful audits — basin is curator-entered and may be wrong.

## 10. Out of scope (follow-ups)

- Multi-HUC reach attribution (long reaches crossing HUC8s).
- Dropping the free-text `reach.basin` column (deferred — it's display-friendly and currator-edited; can be sourced from `huc_name` later).
- Hierarchical filter UI (sketched in §6 but separable).
- Assigning HUC to gauges (`gauge.huc`) — useful for "list all gauges in this watershed" reports, not in current scope.
- Filters by HUC10 or HUC12 (the data supports it; UI complexity probably not worth it for a paddler — HUC8 is the natural "drainage" granularity).
- Auto-deriving `reach.basin` from HUC8 name on insert.

## 11. Estimated diff

- New `scripts/extract_wbd.sh`: ~30 lines.
- New `src/kayak/cli/assign_huc.py` (thin shim — defers GDAL import, mirrors `cli/trace_reach.py`): ~30 lines.
- New `src/kayak/huc/assign.py` (heavy logic — mirrors `kayak/tracing/trace.py`): ~120 lines.
- New `src/kayak/huc/__init__.py`: 1 line.
- New helpers in `src/kayak/db/reaches.py`: ~30 lines (`iter_reaches_with_putin`, `set_reach_huc`, `get_reach_huc_counts`).
- New `HucName` model in `src/kayak/db/models.py`: ~10 lines.
- New tests under `tests/test_huc/test_assign.py`: ~80 lines.
- One-line registration in `src/kayak/cli/main.py`.
- `data/sources.yaml` / pipeline registration: 0 (this is a separate batch job, not part of the per-fetch pipeline).
- Optional: `levels build` change to emit `data-huc12` attribute: ~10 lines.
- Net data: `wbd.gpkg` (~50–100 MB, gitignored) + `huc_name` table (~8.7K rows, ~500 KB).

## 12. Phased landing

| Phase | What | Risk | Reversible |
|---|---|---|---|
| **0** — pre-extract WBD | Add `scripts/extract_wbd.sh`; produce `Trace-cache/wbd.gpkg` once. | None (gitignored data only). | Delete the file. |
| **1** — schema | Add `HucName` model. `levels init-db` picks it up via `Base.metadata.create_all()`. | Tiny — new empty table. | Drop table. |
| **2** — backfill | `levels assign-huc` populates `reach.huc` and `huc_name`. | Low — overwrites only the existing 54 sparse HUC4 values (all derivable as `substr(huc12, 1, 4)`). | Re-NULL `reach.huc` if needed. |
| **3** — periodic timer | `systemd/kayak-assign-huc.{service,timer}` running `levels assign-huc` daily. | Low — same module, idempotent. | Disable timer. |
| **4** — UI (separable) | `data-huc12` attribute + HUC8 pills in `filters.js`. | Low — additive; `data-basin` left in place for one release. | Revert build/JS. |
| **5** — cleanup (later) | Drop `data-basin`, optionally derive `basin` from `huc_name`. | Low. | Restore before-and-after. |

## 13. Open questions for review

1. **Confirm Trace-cache exists on prod**, or is the assignment job run only on dev (with `huc` values shipped via `db_push.sh`)? — Production runs on a 2 vCPU / 2 GB Debian VM; pre-extracting WBD with GDAL there is plausible (~2 min, low memory) but the GDB ZIPs total ~7.7 GB. Recommended: **run the backfill on dev**, push the resulting `reach.huc` + `huc_name` rows via the existing DB sync; don't ship `Trace-cache/` to prod. (Daily timer then runs on dev too.)
2. **Pill granularity**: HUC8 (≈12 per HUC4, ~30–40 distinct in the current reach set) vs HUC10 (~3× more) vs grouping by HUC4 sections of HUC8 pills. §6 picks HUC8 grouped under HUC4 — ok?
3. **Geocode source**: put-in lat/lon vs whole `geom`? §5.4 picks put-in for v1.
4. **Drop `gauge.huc` from scope**, or assign it too (cheap — same script with one extra loop)? Currently §10 punts.
5. **WBD source freshness**: USGS publishes new WBD ~yearly. Document a manual re-extract step in `Trace-cache/README.md`. Auto-update is overkill.

## 14. Reproduce

```sh
DB=${DB:-/home/pat/DB/kayak.db}            # macOS dev: /Users/pat/tpw/DB/kayak.db
TRACE=${TRACE:-/home/pat/kayak/Trace-cache} # macOS dev: /Users/pat/tpw/kayak/Trace-cache

# Confirm reach.huc column already exists with sparse HUC4 data.
sqlite3 "$DB" "SELECT LENGTH(huc), COUNT(*) FROM reach WHERE huc IS NOT NULL AND huc<>''
               GROUP BY LENGTH(huc);"
# expected: only one row with len=4 and count ~54

# Confirm no code reads `huc` (other than the trace_reach `--huc4` arg).
grep -rni 'huc' /home/pat/kayak/src /home/pat/kayak/php 2>/dev/null \
  | grep -v 'models\.py\|test_models\|trace_reach\|tracing'
# expected: empty

# Confirm WBD coverage of the 19 cached HUC4s includes all reach put-ins.
ls "$TRACE/NHD/hr"/NHDPLUS_H_*_HU4_GDB.zip | wc -l       # expected: 19
sqlite3 "$DB" "SELECT MIN(latitude_start), MAX(latitude_start),
                      MIN(longitude_start), MAX(longitude_start)
               FROM reach WHERE latitude_start IS NOT NULL;"
# expected: lat ~41.19–46.93, lon ~-124.28 to -113.99

# Spot-check WBD layer field names (Willamette HUC4 = 1709).
python3 - <<'PY'
from osgeo import ogr; ogr.UseExceptions()
ds = ogr.Open(f'/vsizip/{__import__("os").environ.get("TRACE","Trace-cache")}/NHD/hr/NHDPLUS_H_1709_HU4_GDB.zip/NHDPLUS_H_1709_HU4_GDB.gdb')
for n in ('WBDHU8','WBDHU10','WBDHU12'):
    L = ds.GetLayerByName(n)
    print(n, L.GetFeatureCount(),
          [L.GetLayerDefn().GetFieldDefn(i).GetName() for i in range(L.GetLayerDefn().GetFieldCount())])
PY
# expected: WBDHU12 fields include ('HUC12','Name','States','HUType','ToHUC',...)

# Sanity-check rtree assignment math: how many reaches have geom-able coords?
sqlite3 "$DB" "SELECT
   SUM(CASE WHEN latitude_start IS NOT NULL AND longitude_start IS NOT NULL THEN 1 ELSE 0 END) AS putin,
   COUNT(*) AS total FROM reach;"
# expected: putin ~414, total 425
```
