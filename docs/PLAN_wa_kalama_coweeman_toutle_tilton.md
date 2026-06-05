# Inventory: Kalama, Coweeman, Toutle, Tilton (SW Washington)

Request (2026-06-04): add gauges and reaches for four lower-Columbia /
Cowlitz-basin rivers near Portland, most with AW ids. This doc is the
inventory of AW reaches (including upstream tributaries) and every
related gauge — USGS, NWRFC/NOAA (NWPS), and WA Dept of Ecology — plus
the estimation strategy each river implies. Compiled from
`Gauge-metadata-cache/gauges.db` (AW/NWPS snapshots, 2026-05-19), the
live USGS site service, and the live Ecology FlowMonitoringStations
layer (2026-06-04).

## TL;DR gauge landscape

| River | Live continuous gauge? | Plan |
|---|---|---|
| Tilton | **yes** — USGS 14236200 (1956→, uv) + TILW1 | direct gauge |
| Toutle mainstem | **yes** — USGS 14242580 Tower Rd (1981→, uv) + TOTW1 | direct gauge |
| NF Toutle | **yes** — USGS 14240525 below SRS (1989→, uv) + SRBW1 | direct gauge |
| SF Toutle | no — USGS 14241500 retired **2013** | calc (donors: Tower Rd + NF-SRS; ~21–24 yr overlap) |
| Green (Toutle) | no — USGS 14240800 retired **1994** | calc (donors: Tower Rd 13 yr, NF-SRS 5 yr overlap) |
| Kalama | no — USGS all retired ≤**1982**; DOE 27B070 is manual-only | calc (donor: EF Lewis 14222500, full 1946–82 overlap) |
| Coweeman | no — USGS retired **1984**; DOE 26C075 dead **2019** | calc (donor: EF Lewis; 1950–84 overlap **+ 2006–19 DOE record for out-of-era validation**) |

The common donor, **EF Lewis at Heisson 14222500** (125 mi², 1929→
present, uv 1995→), is already a kayak gauge
(`EF_Lewis_Washington_merge`, gauge 53). None of the Toutle/Tilton
gauges are in the kayak DB yet.

## AW reaches (11 in scope)

### Kalama — no gauge wired on any AW run

| AW id | Section | Class | mi | fpm |
|---|---|---|---|---|
| 2139 | 1. Upper Kalama Falls to Gobar Creek | III–IV | 17.0 | — |
| 2141 | 2. Gobar Creek to Kalama Falls Hatchery | II–IV | 9.8 | 27 |
| 2140 | 3. Lower Kalama Falls to Red Barn / Modrow Road | III–IV | 7.5 | — |

### Coweeman — AW wires the dead DOE station

| AW id | Section | Class | mi | fpm | AW gauge ref |
|---|---|---|---|---|---|
| 3480 | Baird Creek to Jim Watson Creek | II–III(IV) | 11.0 | 41 | wadoe **26C075** (dead since 2019-11) |

### Toutle system

| AW id | Section | Class | mi | fpm | AW gauge ref |
|---|---|---|---|---|---|
| 2253 | Toutle: Hwy 504 Bridge to Tower Rd Bridge | III+(IV) | 9.9 | 32 | USGS 14242580 ✓ (active, at takeout) |
| 3509 | NF Toutle: Green River to SF confluence | II+ | 11.3 | 31 | none — 14240525 sits just above the Green confluence |
| 2254 | SF Toutle: Harrington Place to Big Wolf Creek | II–III | 17.1 | — | USGS 14241500 ✗ (**retired 2013** — stale ref) |
| 2122 | Green River (Toutle drainage): Cascade Cr to Beaver Cr | III–IV | 11.4 | 44 | none |

### Tilton

| AW id | Section | Class | mi | fpm | AW gauge ref |
|---|---|---|---|---|---|
| 3067 | 1. Morton to Bremer (Upper) | II–III | 10.6 | — | USGS 14236200 ✓ (active) |
| 3411 | 2. Bremer to Ike Kinswa State Park (Lower) | III–IV | 8.9 | — | USGS 14236200 ✓ |
| 3430 | NF Tilton: above Tilton confluence | IV | 2.2 | 136 | none (ungauged trib; correlates w/ 14236200) |

(Cache caveat: the `aw_reach.gauges` name for 26C075 reads "East Fork
Lewis, Heisson" — a mis-join; the live Ecology layer confirms 26C075 =
"Coweeman R. nr Kelso". Re-verify against live AW when wiring.)

## USGS gauges

### Active (the four direct-gauge candidates + donor)

| Site | Name | DA mi² | dv record | uv |
|---|---|---|---|---|
| 14236200 | Tilton ab Bear Canyon Cr nr Cinebar | 141 | 1956→present | yes |
| 14242580 | Toutle at Tower Rd nr Silver Lake | 496 | 1981→present | 1992→ |
| 14240525 | NF Toutle below SRS nr Kid Valley | 146 | 1989→present | 2001→ |
| 14222500 | EF Lewis nr Heisson (donor; already kayak g53) | 125 | 1929→present | 1995→ |

### Retired (estimation targets, by priority)

| Site | Name | DA mi² | Record | Days | Notes |
|---|---|---|---|---|---|
| 14241500 | SF Toutle at Toutle | 120 | 1939→**2013** | 13025 | uv 1996–2013 → **lead/lag feasible** |
| 14245000 | Coweeman nr Kelso ("Coweman") | 119 | 1950→**1984** | 12417 | + DOE 26C075 2006–19 telemetry for validation |
| 14223500 | Kalama bl Italian Cr nr Kalama | 198 | 1946→**1982** | 10852 | the long lower-Kalama record |
| 14240800 | Green ab Beaver Cr nr Kid Valley | 129 | 1980→**1994** | 5135 | AW 2122 takeout is Beaver Cr |
| 14241100 | NF Toutle at Kid Valley | 284 | 1980→1994 | 5225 | superseded by 14240525 |
| 14223000 | Kalama nr Kalama | 179 | 1911→1932 | 6360 | alt. Kalama target |
| 14222920/980 | upper Kalama nr Cougar | 12–37 | ≤1982 | — | upper-run scaling refs |
| 14235500 | WF Tilton nr Morton | 16 | 1950→1971 | 7669 | trib history |
| 14236500 | Tilton nr Cinebar | 156 | 1941→1958 | 6268 | pre-14236200 site |
| 14241000 | Green nr Toutle | 131 | 1946→1950 | 1460 | pre-eruption |

Mainstem Cowlitz gauges (Castle Rock 14243000, below Mayfield 14238000,
Packwood 14226500, …) are active but dam-regulated (Mossyrock/Mayfield)
— context only, poor donors.

## NWRFC / NOAA (NWPS) points

| LID | Name | Maps to |
|---|---|---|
| TILW1 | Tilton River near Cinebar | 14236200 |
| TOTW1 | Toutle River at Tower Road | 14242580 |
| SRBW1 | NF Toutle below SRS | 14240525 |
| CASW1 / CLXW1 / KELW1 / MAYW1 / COKW1 / PACW1 / RAWW1 | Cowlitz mainstem chain | regulated, context |
| KLMW1 | **Columbia** at Kalama (not the Kalama River) | — |

No NOAA points on the Kalama, Coweeman, SF Toutle, Green, or any
tributary.

## WA Dept of Ecology (WRIA 26/27)

| Station | Name | Type | Record | Status |
|---|---|---|---|---|
| 26C075 | Coweeman R. nr Kelso | Telemetry | Sep 2006 – Nov 2019 | **Removed** |
| 27B070 | Kalama R. nr Kalama | **Manual stage** | Jul 1997 – present | Active (not telemetered — unusable for nowcast) |
| 26B080 | Cowlitz at Lexington | Telemetry | 2021→ | Active (regulated mainstem) |
| others | EF Lewis ×3, Cedar Cr, Campbell Cr | — | — | all Removed |

## Estimation strategy sketch (to be screened like Smith / SF Salmon)

1. **SF Toutle** (target 14241500, retire 2013): donors Tower Rd
   14242580 (overlap 1981–2013) + NF-SRS 14240525 (1989–2013) — partial
   mass balance, ~21–24 yr of calibration. uv overlap 2001–2013 →
   lead/lag companion feasible. Caveat: 1980 eruption + ongoing SRS
   sediment management make the early Toutle records non-stationary;
   prefer post-1989 window.
2. **Kalama** (target 14223500, retired 1982): donor EF Lewis 14222500
   (full overlap 1946–82, both free-flowing rain-dominated west-slope
   basins, 198 vs 125 mi²). Screen Tilton/Toutle-era/Washougal
   (14143500) as secondary donors.
3. **Coweeman** (target 14245000, retired 1984): donor EF Lewis (full
   overlap 1950–84). Unique asset: the DOE 26C075 telemetry record
   (2006–2019) permits **out-of-era validation** of a 1950–84 fit —
   download from apps.ecology.wa.gov and score the fitted formula
   against it before deploying.
4. **Green (Toutle)** (target 14240800, retired 1994): donors Tower Rd
   (1981–94) ± NF-SRS (1989–94). Short window; eruption-era caveats.
5. **NF Toutle run (AW 3509)**: starts at the Green confluence — value ≈
   14240525 (live) + Green estimate; or just wire 14240525 directly
   with a note that the Green's contribution is unrepresented.
6. **Tilton runs + NF Tilton**: direct 14236200; NF Tilton trib reach
   can share the Tilton gauge like other trib reaches do.

Wiring order suggestion: direct-gauge rivers first (Tilton, Toutle,
NF Toutle — new USGS gauges/sources + reaches), then the four calc
screenings, then the Kalama/Coweeman/SF-Toutle/Green reaches.

## Reproduce

```bash
# AW reaches (cache, 2026-05-19 snapshot)
sqlite3 Gauge-metadata-cache/gauges.db \
  "SELECT id, river, section, class, gauges FROM aw_reach WHERE state='WA' AND
   (river LIKE '%kalama%' OR river LIKE '%coweeman%' OR river LIKE '%toutle%'
    OR river LIKE '%tilton%' OR river LIKE '%green%' COLLATE NOCASE);"

# USGS catalogs
curl -s 'https://waterservices.usgs.gov/nwis/site/?format=rdb&huc=17080003,17080005&parameterCd=00060&siteType=ST&seriesCatalogOutput=true&outputDataTypeCd=dv,iv'

# NWPS points (cache table nwps_site), Ecology stations (live layer)
curl -s -A Mozilla/5.0 "https://gis.ecology.wa.gov/serverext/rest/services/EAP/FlowMonitoringStations/MapServer/0/query?where=StationID+LIKE+'26%25'+OR+StationID+LIKE+'27%25'&outFields=StationID,StationName,TypeDescription,PeriodOfRecord,Status&returnGeometry=false&f=json"
```
