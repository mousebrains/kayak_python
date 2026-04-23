# Whitewater Guidebook (WGB) reach matches

Compiled 2026-04-22 from `whitewaterguidebook.com/{idaho,california,washington,oregon}/`
against the `reach` table (post-ID-compaction IDs). `guidebook_id = 13`.

## Confidence scoring

| Score | Meaning |
|---|---|
| **5** | Definitive: put-in + take-out coords + length + class all match within tolerance |
| **4** | Strong: 2 of 3 (coords/length/class) exact, 1 close; or clearly the same run with minor measurement drift |
| **3** | Probable: run identity strong (name, river, same general stretch) but one end or length differs materially |
| **2** | Possible: partial overlap — WGB describes a subsection of a DB reach, or vice versa |
| **1** | Weak: only the river is shared |

## Legend

- **✓ inserted** — row exists in `reach_guidebook` (this session)
- **~ pre-existing** — already in DB before this session (may be worth revisiting)
- **◯ candidate** — not inserted; revisit later
- **✗ no match** — WGB run has no reach in our DB

---

## Idaho

### ✓ Inserted (7)

| Score | Reach | DB stretch | WGB page | Match evidence |
|---|---|---|---|---|
| 5 | 128 MF Salmon | Boundary Creek → Salmon | /idaho/middle-fork-salmon-river/ | exact put-in/take-out, III-IV |
| 5 | 126 Salmon | Corn Creek → Carey Creek | /idaho/main-salmon-river/ | classic "River of No Return" |
| 5 | 142 Selway | Paradise → Selway Falls | /idaho/selway-river/ | exact |
| 5 | 130 Lochsa | Fish Creek → Split Creek | /idaho/lochsa-river/ | Fish Creek Launch → Split Rock Pack Bridge |
| 5 | 133 SF Salmon | Secesh → Salmon | /idaho/south-fork-salmon-river/ | WGB: Secesh confluence → Vinegar Creek; reach is SF portion |
| 4 | 132 Salmon | White Bird → Snake | /idaho/lower-salmon-river/ | WGB: Hammer Creek → Heller Bar 72 mi; reach is Salmon portion |
| 4 | 127 Jarbidge | Forks → Bruneau | /idaho/jarbidge-bruneau/ | WGB: Jarbidge Forks → Hot Springs Rd 71 mi; reach is Jarbidge portion |

### ◯ Candidates

| Score | Reach | DB stretch | WGB page | Notes |
|---|---|---|---|---|
| 1 | 375 SF Payette | Danskin → Alder Creek (5.85 mi) | /idaho/south-fork-of-the-payette-staircase/ | WGB Staircase = Deer Creek → Banks (4.6 mi). Reach 375 take-out is ~8 mi upstream of Banks; different run. |

### ✗ Not in DB

Big Creek, Bear Valley Creek, Panther Creek, Snake (Hagerman), Snake (Murtaugh).

---

## California

### ✓ Inserted (7)

| Score | Reach | DB stretch | WGB page | Match evidence |
|---|---|---|---|---|
| 5 | 122 NF Smith | Major Moore's → Gasquet (14.8 mi III-IV) | /california/north-fork-smith-river/ | exact length (14.8 mi = 14.8 mi) |
| 4 | 370 Diamond Creek | End of Rd 18N09 → NF Smith confluence (4.1 mi) | /california/diamond-creek/ | WGB "4.3 mi on creek, then 11 on NF"; ends at confluence |
| 4 | 124 SF Smith | Steven Bridge → ~Jed Smith (11.9 mi II-IV+) | /california/south-fork-smith-river/ | put-in exact; length 11.9 vs WGB 16.1 mi (diff measuring) |
| 4 | 125 SF Smith | South Kelsey → Steven Bridge (7.8 mi IV(V)) | /california/upper-south-fork-of-the-smith-river/ | class matches exactly (IV(V)); length 7.8 vs 6.5 mi |
| 4 | 121 Klamath | Happy Camp → ~Orleans (17.9 mi III+(IV)) | /california/lower-klamath-river/ | put-in = Happy Camp; 1st half of WGB 37-mi trip |
| 4 | 119 Klamath | ~Orleans → Somes Bar (19.5 mi II) | /california/lower-klamath-river/ | 2nd half; take-out ≈ Green Riffle |
| 3 | 123 MF Smith | 1.2 mi IV-V gorge | /california/oregon-hole-gorge/ | AW aw_11621 + Soggy Sneakers p.67 both list OHG distinctly from main MF; WGB ends at Forks confluence (~18 km downstream of reach 123 take-out) |

### ◯ Candidates

| Score | Reach | DB stretch | WGB page | Notes |
|---|---|---|---|---|
| 3 | 114 MF Smith | Patrick → Forks confluence (16.5 mi II-V) | /california/middle-fork-of-the-smith-river-below-patrick/ | WGB: Patrick → Grassy Flat 3.2 mi III-IV. Same put-in; WGB is first ~3 mi of reach 114. |
| 3 | 372 SF Smith | below reach 124 → Smith confluence (1.8 mi) | /california/south-fork-smith-river/ (supplement to 124) | Covers the final 1.8 mi of WGB's "16.1 mi Steven Bridge → Jed Smith" route. |

### ✗ Not in DB

Hardscrabble Creek, Goose Creek, Scott River, Cal Salmon, Redwood Creek,
MF Feather, NF/MF/SF American, Stanislaus (Camp 9), NF Stanislaus, Tuolumne,
Cherry Creek, Merced, Kaweah, Upper Kern, Lower Kern, Forks of the Kern.

---

## Washington

### ✓ Inserted (5)

| Score | Reach | DB stretch | WGB page | Match evidence |
|---|---|---|---|---|
| 5 | 37 Little Klickitat | Esteb Rd → Klickitat confluence (9.9 mi) | /washington/little-klickitat-river/ | 9.9 vs WGB 10 mi; put-in + take-out both match |
| 5 | 382 Wind | Stabler → High Bridge (6.5 mi) | /washington/upper-wind-river/ | 6.5 vs 6.6 mi; put-in/take-out match |
| 5 | 297 White Salmon | BZ → Husum (4.38 mi III+(V)) | /washington/white-salmon/ | exact BZ → Husum, 4.38 vs 4.6 mi |
| 4 | 294 Klickitat | Hatchery → Leidl (15.8 mi II) | /washington/klickitat-river/ | both endpoints match named locations; our length is 50% higher than WGB's 10.5 mi (likely measurement diff) |
| 4 | 298 Klickitat | Leidl → Pitt/Icehouse (11.2 mi II-III) | /washington/klickitat-river-below-leidl/ | put-in = Leidl; take-out = Old Icehouse Access |

### ◯ Candidates

| Score | Reach | DB stretch | WGB page | Notes |
|---|---|---|---|---|
| 3 | 295 Klickitat | 22.3 mi III+, ends at Hatchery | /washington/upper-klickitat-river/ | Take-out = Hatchery; difficulty III+ matches. But reach is 22.3 mi vs WGB 8.1 mi (Parrot's Crossing → Hatchery). Reach 295 put-in is upstream of Parrot's. |
| 3 | 299 White Salmon | Husum → Columbia confluence (4.9 mi II-III+(V)) | /washington/white-salmon-narrows/ | WGB content describes Northwestern Park → Columbia, 5 mi, III+ w/ Steelhead Falls (V). Reach 299 put-in is above Northwestern Park. Length 4.9 vs 5 mi. |
| 2 | 378 White Salmon | Green Truss → BZ (5.3 mi) | /washington/white-salmon-river-orletta/ | WGB Orletta = Orletta Creek confluence → BZ, 2.2 mi IV/IV+. Orletta is the lower portion of reach 378. |
| 2 | 381 Wind | Falls Creek area → Stabler (7.4 mi) | /washington/upper-upper-wind-river/ | WGB Upper Upper = Falls Creek turnout → Mineral Springs Bridge, 4 mi IV(P). Reach 381 is 7.4 mi (superset extending to Stabler). |
| 1 | — | Washougal reaches 385-388 | /washington/washougal-river/ | WGB Big Eddy = 10-Mile Bridge → Hathaway, 9.9 mi III(IV). Our reaches have a gap near the WF confluence; no single reach matches. |

### ✗ Not in DB

Sol Duc, Elwha, EF Humptulips Narrows, Upper Upper Sitkum, Hamma Hamma,
Green River Gorge, Lower Tilton, SF Snoqualmie (Fall in the Wall),
Canyon Creek of SF Stilly, NF Nooksack Horseshoe Bend, Upper Cispus,
Yellowjacket Creek, Tieton, EF Lewis, Canyon Creek of Lewis, Siouxon Creek.

---

## Oregon

### ✓ Inserted (1)

| Score | Reach | DB stretch | WGB page | Match evidence |
|---|---|---|---|---|
| 5 | 61 Owyhee | Rome → Leslie Gulch (68.2 mi) | /oregon/owyhee-river/ | WGB: Rome → Leslie Gulch 65 mi (or Birch Creek 48 mi); reach is the full lower trip |

### ~ Pre-existing rows worth revisiting

(PK constraint `(reach_id, guidebook_id)` means each reach can carry only
one WGB URL. These are single-URL reaches where the current URL may be
suboptimal; swapping would lose the other target.)

| Reach | DB stretch | Current URL | Better/additional URL | Notes |
|---|---|---|---|---|
| 235 White River | Keeps Mill → White River Crossing (11.2 mi, coords confirm Upper) | /oregon/lower-white-river/ | /oregon/white-river-keeps-milll-white-crossing/ | Current URL describes White River Crossing → Tygh Valley (Lower), but reach coords match the Upper. Current link is miscategorized. |
| 47 Metolius | Riverside → Monty (26 mi) | /oregon/lower-metolius-river/ | also /oregon/upper-metolius-river/ | Reach covers WGB's Upper (10 mi Riverside → Lower Bridge) + Lower (16 mi Lower Bridge → Monty). Current URL captures only half. |

### ✗ Not in DB

Rough and Ready Creek, NF Crooked River, Wenaha River.

---

## Schema note

`reach_guidebook` has `PRIMARY KEY (reach_id, guidebook_id)`. One reach can
hold at most one URL per guidebook. The inverse (one URL → multiple reaches)
is used e.g. `/oregon/upper-klamath-river/` → reaches 31, 32, 115. So when a
WGB page describes a multi-reach run (Lower Klamath, Lower Salmon,
Jarbidge-Bruneau), we link the same URL to each covered reach. When a DB
reach spans multiple WGB runs (reach 47 Metolius, reach 235 White River),
we can pick only one.

## Data sources cross-checked during matching

- **AW (`americanwhitewater.org`)** IDs embedded in `reach.name` (`aw_NNN`) —
  used as tiebreaker for named runs when WGB didn't give coords.
- **Soggy Sneakers** page numbers in `reach_guidebook` — separate page entries
  signal that a reach is a distinct classic run (used to confirm reach 123
  is Oregon Hole Gorge, for example).
- Reach `latitude_start/end`, `longitude_start/end` — primary geographic key.

## Note on GPS

Spot-checked several WGB pages (MF Salmon, SF Smith, Upper Wind). Pages
contain **no** GPS coordinates — no lat/lon, no embedded maps, no Google
Maps / OSM links, no KML/GPX. Matching required landmark name lookup.
