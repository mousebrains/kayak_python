# Stream Trace Pipeline

Generate GPS traces for river reaches using NHDPlus High Resolution network data.

## Quick Start

```bash
# One-time: pre-extract HUC4 GDBs into fast GeoPackages (parallel, ~20 min)
bash scripts/extract_trace_data.sh

# Trace a reach
levels trace \
    --putin 42.694599,-116.400002 \
    --takeout 42.237221,-116.523888 \
    --name "Battle Creek"
```

Outputs three files:

* `battle_creek_trace.csv` — `latitude,longitude` per row (debug / inspection).
* `battle_creek_trace.geom.sql.txt` — single-line **SQL-ready geom string**
  in the canonical `"lon lat,lon lat,…"` format for pasting directly
  into a migration's `reach.geom` column. **Do not wrap in
  `LINESTRING(…)`** — the PHP map parser at
  `php/includes/gauge_map.php:61-70` splits on commas and float-casts
  each side; a wrapper produces a `(0°, lat)` first vertex (somewhere
  in the Atlantic) and the polyline draws a long horizontal line.
  See migration `0041` for the original bug; the format helper at
  `kayak.tracing.format.format_geom_for_sql` is what `levels trace`
  uses to emit the file correctly.
* `battle_creek_trace.png` — trace rendered on OpenTopoMap. Use
  `--csv-only` to skip the map.

After landing a trace-fed migration, run `levels check-reaches` —
it scans every `reach.geom` for WKT wrappers, out-of-range
coordinates, malformed pairs, and >300m drift between the first/last
vertex and the `latitude_start` / `longitude_start` / `latitude_end` /
`longitude_end` columns. Returns exit code 1 on any issue.

## Data Source

**NHDPlus High Resolution** HUC4 GeoDatabase ZIP files from USGS, stored in
`Trace-cache/NHD/hr/`. Each ZIP is 90 MB–1.3 GB and contains 85 layers. We use
exactly **2 layers**:

| Layer | Example Size (HUC4 1705) | Has Geometry | Fields Used |
|---|---|---|---|
| `NHDFlowline` | 178,023 features | Yes (3D MultiLineString) | `NHDPlusID`, `GNIS_Name`, geometry |
| `NHDPlusFlowlineVAA` | 177,204 features | No (attribute table) | `NHDPlusID`, `HydroSeq`, `DnHydroSeq` |

The other 83 layers (catchments, precipitation, temperature, burn lines, watershed
boundaries, junctions, runoff, etc.) are unused.

### Why HydroSeq, not NHDPlusFlow?

The GDB also contains `NHDPlusFlow` (from/to NHDPlusID pairs). We tried it first —
BFS fails because it has connectivity gaps. The `NHDPlusFlowlineVAA` table's
`HydroSeq → DnHydroSeq` chain provides gap-free downstream connectivity.

### NHD vs OSM

We compared NHD and OSM traces on two test reaches:

- **Battle Creek, ID (60 mi):** NHD has no OSM coverage at all in this area.
  NHD trace: 60.7 mi (4,443 pts). AW reference: 60.5 mi.
- **Grande Ronde, OR (59 mi):** NHD is 3.4× denser than OSM (22.7 vs 6.6 pts/km).
  OSM has large gaps in the rural middle section. NHD wins on both coverage and density.

NHD is DEM/lidar-derived and has better coverage and resolution than OSM for rural
streams in the Pacific Northwest. NHD may still miss tight meanders due to DEM
resolution limits. OSM PBF files are retained in `Trace-cache/OSM/` in case they
prove useful for urban/suburban streams where OSM contributors have traced detail.

## Algorithm

### Step 1: Determine the HUC4

Each reach falls within a HUC4 hydrologic unit. The tracer auto-detects the
HUC4 by finding the **nearest flowline** to the put-in across every candidate
GPKG (point-to-line distance, same query as Step 3), then confirming the
take-out resolves to the same HUC4 — endpoint agreement guards against picking
a neighbour across a basin divide. (The earlier heuristic — first GPKG whose
flowline *extent* contained the put-in — mis-detected 88 of 407 reaches near
divides, because bounding boxes overlap.) Override with `--huc4 1705`.

We have 19 HUC4s covering OR, WA, ID, NV, CA (1601–1803).

### Step 2: Load the VAA network index

Read every row from the `vaa` table (3 fields: `NHDPlusID`, `HydroSeq`,
`DnHydroSeq`), building two in-memory dicts:

```python
by_hydroseq[HydroSeq] = (NHDPlusID, DnHydroSeq)
by_nhdpid[NHDPlusID]  = (HydroSeq, DnHydroSeq)
```

This takes ~2.4s and ~19 MB per HUC4.

### Step 3: Find the start and end flowlines

Spatial query on the `flowline` layer with a 0.15° buffer around the put-in and
take-out coordinates. The nearest flowline (by point-to-line distance) to each
is the start/end. With the pre-extracted GPKG spatial index, this is <0.1 seconds.

### Step 4: Follow the HydroSeq chain downstream

```python
current_hseq = by_nhdpid[start_id].hseq
path = []
while current_hseq:
    nhdpid, dn_hseq = by_hydroseq[current_hseq]
    path.append(nhdpid)
    if nhdpid == end_id:
        break
    current_hseq = dn_hseq
```

O(n) in path length — typically 30–200 segments per reach.

If the exact take-out flowline isn't on the main stem (e.g., the take-out is on the
Owyhee River but the HydroSeq chain follows Battle Creek past it), the script traces
an extended path and trims to the segment nearest the take-out.

### Step 5: Assemble coordinates

Load `NHDFlowline` geometries for just the path segments. Linearize any compound
curves, reverse individual segments as needed so each connects to the previous,
and orient the full trace from put-in to take-out.

### Step 6: Calculate distance

Sum haversine distances between consecutive points.

## Pre-extraction (one-time setup)

Reading directly from the raw GDB ZIPs works but spatial queries take ~63 seconds
(full scan, no spatial index). Pre-extracting to GeoPackage gives <0.1s queries.

```bash
bash scripts/extract_trace_data.sh
```

This extracts just the 2 needed layers from each of the 19 HUC4 GDB ZIPs into
`Trace-cache/trace/trace_{HUC4}.gpkg`. Runs 4 extractions in parallel via a
FIFO-based job pool (compatible with macOS bash 3.2). Takes ~20 minutes total.

| Format | Total Size | Spatial Query Time |
|---|---|---|
| Raw GDB ZIPs (`NHD/hr/`) | 7.7 GB | ~63s per query |
| Pre-extracted GPKGs (`trace/`) | 5.2 GB | <0.1s per query |

`trace_reach.py` automatically prefers GPKGs when available, falling back to raw ZIPs.

## File Inventory

```
Trace-cache/                         # Gitignored
├── NHD/hr/                          # Raw NHDPlus HR GDB ZIPs (7.7 GB)
│   ├── NHDPLUS_H_1601_HU4_GDB.zip
│   ├── ...
│   └── NHDPLUS_H_1803_HU4_GDB.zip  # 19 HUC4s total
├── OSM/                             # OSM PBF files (retained)
│   ├── oregon-latest.osm.pbf
│   └── ...                          # CA, ID, NV, WA
├── trace/                           # Pre-extracted GPKGs (5.2 GB)
│   ├── trace_1601.gpkg
│   ├── ...
│   └── trace_1803.gpkg              # 19 files
└── README.md

scripts/
├── trace_reach.py                   # Trace a single reach (CLI tool)
├── extract_trace_data.sh            # Pre-extract HUC4 GDBs → GPKGs
├── fetch_nhd.sh                     # Download raw NHD HR GDBs from USGS S3
├── extract_nhd_flowlines.sh         # (legacy) Extract named flowlines for merge
├── extract_osm_waterways.sh         # (legacy) Extract OSM waterways for merge
└── merge_flowlines.py               # (legacy) Merge NHD+OSM named streams
```

The legacy scripts (`extract_nhd_flowlines.sh`, `extract_osm_waterways.sh`,
`merge_flowlines.py`) built `named_flowlines.gpkg` and `merged_flowlines.gpkg` for
display-quality named stream geometry. These are not used for tracing — the merge
fragments streams by name, losing upstream-to-downstream connectivity.

## Caveats and Edge Cases

1. **Cross-HUC4 reaches:** A reach crossing HUC4 boundaries needs data from both
   GDBs. The HydroSeq chain may break at the boundary.

2. **Divergences:** Where a stream splits (braids, irrigation diversions), the
   HydroSeq chain follows the main stem. The `Divergence` field in the VAA table
   marks minor paths (value=2).

3. **Unnamed headwater segments:** `GNIS_Name` is often NULL for small headwater
   segments. Tracing still works — it follows HydroSeq regardless of naming.

4. **Put-in/take-out off the stream:** If coordinates are slightly off the stream
   (common for road access points), the nearest-flowline search handles this.
   Typical tolerance: ~0.001° (~100m).

5. **Meander resolution:** NHD geometry is DEM/lidar-derived at ~20–25 pts/km.
   Tight meanders may be smoothed. This is a source data limitation — OSM is
   typically coarser, not denser, for rural Pacific NW streams.
