# Plan — PacifiCorp Rogue Bypass parser + Rogue-above-Prospect calc gauge

**Status:** Open (2026-05-17). Ships in one PR: parser + sources.yaml + migration 0027.

## Goal

Two related additions to the Rogue River data:

1. **New `pacificorp` XML parser** consuming `https://www.pacificorp.com/etc/pcorp/datafiles/hydro/RogueRiverBypass.xml` for the **North Fork Rogue bypassed reach** (Class V whitewater section between the North Fork Diversion Dam and Prospect No. 2 powerhouse, FERC Article 415 reporting). Feeds reach 68 ("Mill" run), previously pointing at dormant calc gauge 89.
2. **`Rogue_Above_Prospect_calc` gauge** that estimates the retired **USGS 14328000** ("Rogue above Prospect", silent since 2024-06-09) from the still-active **USGS 14330000** ("Rogue below Prospect") via a fitted linear relationship. Resurrects flow on reaches 67 (River Bridge), 161 (Takelma), and 307 (Natural Bridge) that previously pointed at the retired gauge (or its sibling dormant calcs).

## Existing Rogue gauge / reach picture (pre-migration snapshot)

| Gauge id | Name | usgs_id | Reaches pointing at it | Status |
|---|---|---|---|---|
| 87 | `NF_ROGUE_LOST_CREEK_calc` | — | 67 (River Bridge) | Dormant — no sources, no calc_expression |
| 88 | `14328000` | 14328000 | 161 (Takelma), 307 (Natural Bridge) | USGS gauge retired 2024-06-09 |
| 89 | `MF_ROGUE_LOST_CREEK_calc` | — | 68 (Mill / Prospect Bypass) | Dormant — no sources, no calc_expression |

After migration 0027:

| Gauge | Sources | Reaches pointing at it |
|---|---|---|
| `NF_Rogue_Bypass` (new) | PacifiCorp `PR2R.NFD_BYP_80FL_PI` | 68 |
| `14330000` (new) | USGS 14330000 via `fetch-usgs-ogc` | — (referenced by calc) |
| `Rogue_Above_Prospect_calc` (new) | calc source w/ `0.8285 * rp::14330000::flow - 292.72` | 67, 161, 307 |
| 87, 88, 89 | (unchanged) | — (deletion deferred to follow-up) |

## XML feed shape

```xml
<Measurements>
  <Measurement>
    <PowerSystemResourceId>PR2R.NFD_BYP_80FL_PI</PowerSystemResourceId>
    <MeasurementUnit>csf</MeasurementUnit>          <!-- typo for "cfs" in the feed -->
    <MeasurementValue>
      <timeStamp>2026-05-11 01:59:59</timeStamp>     <!-- naive local time -->
      <value>88</value>
      <MeasurementValueQuality><validity>0</validity></MeasurementValueQuality>
    </MeasurementValue>
    ...
  </Measurement>
  <RequestedStartDate>...</RequestedStartDate>
  <RequestedEndDate>...</RequestedEndDate>
</Measurements>
```

Parser handling:
- Accept both `csf` (the published typo) and `cfs` units → `DataType.flow`.
- Naive timestamps in `America/Los_Angeles` (DST-aware). Localization handled by `BaseParser.dump_to_db` via the `source_tz_map` from `sources.yaml`'s `stations:` block. Parser emits naive datetimes via `parse_datetime(..., assume_naive=True)`.
- `validity == 0` is good; `validity != 0` (commonly `-248` for the in-progress hour with `value="n/a"`) is dropped.

## Linear relationship for the calc gauge

Both gauges are USGS daily-mean flow (parameterCd `00060`, statCd `00003`). Total overlap across history: **17,091 daily means from 1923-10-01 to 2024-06-09**, non-contiguous (14328000 was offline ≈1998 to mid-2014).

### Window choice (≥20 years of overlap data)

A calendar window like "2000-2024" only contains 9.8 years of overlap (3579 daily means) because 14328000 was offline for most of that calendar range. To get **≥20 years of data points**, the window must reach back to ~1985.

| Window start (end = 2024-06-09) | Calendar yr | Data yr (n/365.25) | n | slope | intercept | r² | RMSE (cfs) |
|---|---|---|---|---|---|---|---|
| 1990-01-01 | 34.5 | 18.5 | 6773 | 0.8318 | −271.78 | 0.9655 | 110.1 |
| **1985-01-01** (chosen) | **39.4** | **23.5** | **8599** | **0.8285** | **−292.72** | **0.9575** | **117.1** |
| 1980-01-01 | 44.4 | 28.5 | 10426 | 0.8288 | −307.66 | 0.9577 | 119.2 |
| 1975-01-01 | 49.4 | 33.5 | 12252 | 0.8317 | −321.63 | 0.9582 | 116.8 |
| 1970-01-01 | 54.4 | 38.5 | 14078 | 0.8399 | −343.73 | 0.9585 | 123.8 |
| 1923-10-01 (all) | 100.8 | 46.8 | 17091 | 0.7956 | −240.30 | 0.9267 | 159.2 |

Post-1970 the fit is **remarkably stable**: slope ≈ 0.83, intercept ≈ −300 to −340, r² ≈ 0.96. Going back into the pre-Lost-Creek-Lake era (regulation began 1977) degrades the fit (slope 0.80, r² 0.93).

### Chosen fit

```
14328000_est = 0.8285 × 14330000 − 292.72
r² = 0.9575, RMSE = 117.1 cfs, n = 8599 daily means
window = 1985-01-01 to 2024-06-09 (non-contiguous; 14328000 offline 1998-2014)
mean(y) = 788 cfs, mean(x) = 1304 cfs
x range = [482, 10500] cfs, y range = [222, 9600] cfs
```

Identity `y = x` is **not** valid — 14328000 measures above the diversion dam, 14330000 below the powerhouse rejoin (per local geography). The slope ~0.83 captures the tributary-inflow shift between the two gauges.

### Residual diagnostics

Percentile distribution (residual = y − ŷ, cfs):

| p01 | p05 | p25 | p50 | p75 | p95 | p99 |
|---|---|---|---|---|---|---|
| −237.6 | −171.0 | −83.2 | +2.2 | +79.8 | +152.9 | +327.7 |

By 14330000 flow quintile:

| Quintile | x median (cfs) | mean residual | std residual | n |
|---|---|---|---|---|
| Q1 | 692 | +80.6 | 55.6 | 1719 |
| Q2 | 913 | −0.7 | 72.5 | 1719 |
| Q3 | 1120 | −31.0 | 94.6 | 1719 |
| Q4 | 1460 | −49.7 | 100.0 | 1719 |
| Q5 | 2100 | +0.8 | 177.1 | 1723 |

Slight under-estimate at low flows (Q1: +80 cfs mean) and over-estimate at moderate flows (Q3/Q4), with much higher variance at high flows (Q5: σ=177 cfs). For paddling-window flows residuals are well-bounded.

### Reproduce

```bash
curl -s "https://waterservices.usgs.gov/nwis/dv/?format=rdb&sites=14328000&startDT=1900-01-01&endDT=2026-05-17&parameterCd=00060&statCd=00003" -o /tmp/14328000_dv.tsv
curl -s "https://waterservices.usgs.gov/nwis/dv/?format=rdb&sites=14330000&startDT=1900-01-01&endDT=2026-05-17&parameterCd=00060&statCd=00003" -o /tmp/14330000_dv.tsv
python3 - <<'PY'
import math
def load(p):
    out={}
    for line in open(p):
        if line.startswith('#') or not line.strip(): continue
        parts=line.split('\t')
        if parts[0]=='agency_cd' or parts[0].startswith('5s'): continue
        if len(parts)<4: continue
        try: out[parts[2]]=float(parts[3])
        except ValueError: pass
    return out
def fit(pts):
    n=len(pts); sx=sum(x for x,_ in pts); sy=sum(y for _,y in pts)
    sxx=sum(x*x for x,_ in pts); sxy=sum(x*y for x,y in pts)
    m=(n*sxy-sx*sy)/(n*sxx-sx*sx); b=(sy-m*sx)/n
    ss_r=sum((y-(m*x+b))**2 for x,y in pts); ss_t=sum((y-sy/n)**2 for _,y in pts)
    return m, b, 1-ss_r/ss_t, math.sqrt(ss_r/n), n
a=load('/tmp/14328000_dv.tsv'); b=load('/tmp/14330000_dv.tsv')
pts=[(b[d],a[d]) for d in sorted(set(a)&set(b)) if '1985-01-01'<=d<='2024-06-09']
m,b0,r2,rmse,n = fit(pts)
print(f"FINAL: 14328000_est = {m:.4f} * 14330000 + ({b0:.2f})")
print(f"       r² = {r2:.4f}, RMSE = {rmse:.1f} cfs, n = {n}")
PY
```

## Files touched

| Path | Type |
|---|---|
| `src/kayak/parsers/pacificorp.py` | new — XML parser, lxml-hardened |
| `src/kayak/parsers/registry.py` | edit — add `pacificorp` to `ensure_all_loaded()` import |
| `data/sources.yaml` | edit — new `pacificorp:` block with `stations:` timezone map |
| `tests/test_parsers/test_pacificorp_records.py` | new — 9 cases on `parse_records` |
| `data/db/migrations/0027_wire_pacificorp_and_rogue_above_prospect.sql` | new — Parts A-D: PacifiCorp wiring + USGS 14330000 gauge + calc gauge + reach re-pointing |
| `docs/PLAN_pacificorp_rogue.md` | this file |

## Follow-up (deliberately not in this PR)

- Delete gauges 87, 88, 89 once `orphan-check` confirms no other consumers.
- Re-fit if the relationship drifts (e.g. tributary diversions change, or 14328000 reactivates).
- Rename `NF_Rogue_Bypass` to match the `_calc`/`_merge` suffix convention (cosmetic; editor UI handles it).
