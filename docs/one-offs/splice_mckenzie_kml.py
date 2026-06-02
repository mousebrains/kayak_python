#!/usr/bin/env python3
"""Splice the user-supplied McKenzie.kml main channel into reaches 42 & 421.

Both NHD-HR traces detour through the Rainbow north side channels near Bruckart.
The user hand-drew the true paddled channel through the braids (McKenzie.kml,
one polyline upstream→downstream spanning both reaches' problem stretches). We
keep each reach's good NHD trace outside the braids and substitute the KML
inside:

  reach 42  (upper): NHD[put-in .. KML start] + KML[.. Bruckart] + Bruckart
  reach 421 (lower): Bruckart + KML[Bruckart ..] + NHD[KML end .. take-out]

Writes geom + recomputed length + arc-length midpoint. Run under .venv (no
osgeo needed). Elevation/gradient are recomputed by the usual tools afterward.
"""

from __future__ import annotations

import math
import re
import sqlite3

KML = "/Users/pat/tpw/kayak/McKenzie.kml"
DB = "/Users/pat/tpw/DB/kayak.db"
BRUCKART = (-122.260364, 44.163731)  # (lon, lat) — the shared take-out/put-in


def hav(lon1, lat1, lon2, lat2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def length_mi(pts):
    return sum(hav(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1)) / 1609.344


def maxseg(pts):
    return max(hav(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))


def midpoint(pts):
    segs = [hav(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1)]
    half = sum(segs) / 2
    acc = 0.0
    for i, s in enumerate(segs):
        if acc + s >= half:
            t = (half - acc) / s if s else 0
            lon = pts[i][0] + t * (pts[i + 1][0] - pts[i][0])
            lat = pts[i][1] + t * (pts[i + 1][1] - pts[i][1])
            return round(lon, 6), round(lat, 6)
        acc += s
    return pts[-1]


def nearest_idx(pts, tgt):
    return min(range(len(pts)), key=lambda i: hav(pts[i][0], pts[i][1], tgt[0], tgt[1]))


with open(KML, encoding="utf-8") as fh:
    txt = fh.read()
# The KML may carry several <LineString>s (a MultiGeometry) — the channel split
# into contiguous segments. Concatenate them in document order (upstream ->
# downstream), dropping a near-duplicate point at each segment join (<5 m).
kml = []
for block in re.findall(r"<coordinates>(.*?)</coordinates>", txt, re.S):
    for tok in block.split():
        lon, lat = float(tok.split(",")[0]), float(tok.split(",")[1])
        if kml and hav(kml[-1][0], kml[-1][1], lon, lat) < 5:
            continue
        kml.append((lon, lat))
print(f"KML: {len(kml)} pts, first {kml[0]}, last {kml[-1]}")

db = sqlite3.connect(DB)


def geom(rid):
    g = db.execute("SELECT geom FROM reach WHERE id=?", (rid,)).fetchone()[0]
    return [tuple(map(float, p.split())) for p in g.split(",")]


r42, r421 = geom(42), geom(421)

# Split the KML at Bruckart: last point east of (lon >= ) Bruckart belongs to r42.
b_split = max(i for i, (lon, _) in enumerate(kml) if lon >= BRUCKART[0])
print(f"KML Bruckart split: idx {b_split} {kml[b_split]} | next {kml[b_split + 1]}")

# Reach 42: keep NHD up to the KML's eastern start, then KML to Bruckart.
idx_e = nearest_idx(r42, kml[0])
seam42 = hav(*r42[idx_e], *kml[0])
new42 = r42[: idx_e + 1] + kml[1 : b_split + 1] + [BRUCKART]

# Reach 421: Bruckart, then the KML west of it. If the KML now reaches the
# take-out (Finn Rock), finish exactly at the take-out with no NHD tail;
# otherwise splice the NHD trace from the KML's western end down to the take-out.
FINNROCK = (-122.380165, 44.128451)  # reach 421 take-out column (lon, lat)
west = kml[b_split + 1 :]
if hav(*west[-1], *FINNROCK) < 200:
    idx_w = None
    seam421 = hav(*west[-1], *FINNROCK)
    new421 = [BRUCKART, *west, FINNROCK]
else:
    idx_w = nearest_idx(r421, kml[-1])
    seam421 = hav(*r421[idx_w], *kml[-1])
    new421 = [BRUCKART, *west, *r421[idx_w + 1 :]]

anchor421 = (
    "KML reaches take-out (no NHD tail)"
    if idx_w is None
    else f"cut NHD@{idx_w} (kept {len(r421) - idx_w - 1} of {len(r421)})"
)
for rid, old, new, seam, anchor in [
    (42, r42, new42, seam42, f"cut NHD@{idx_e} (kept {idx_e + 1} of {len(r42)})"),
    (421, r421, new421, seam421, anchor421),
]:
    print(f"\n=== reach {rid} ===")
    print(f"  {anchor}; splice seam = {seam:.1f} m")
    print(
        f"  verts {len(old)} -> {len(new)};  length {length_mi(old):.2f} -> {length_mi(new):.2f} mi"
    )
    print(f"  max segment in new geom: {maxseg(new):.0f} m")
    print(f"  start {new[0]}  end {new[-1]}")

ans = input("\nWrite these geoms to the DB? [y/N] ")
if ans.strip().lower() == "y":
    for rid, new in [(42, new42), (421, new421)]:
        g = ",".join(f"{lon:.6f} {lat:.6f}" for lon, lat in new)
        mlon, mlat = midpoint(new)
        db.execute(
            "UPDATE reach SET geom=?, length=?, latitude=?, longitude=? WHERE id=?",
            (g, round(length_mi(new), 1), mlat, mlon, rid),
        )
    db.commit()
    print("written.")
else:
    print("aborted (no changes).")
db.close()
