#!/usr/bin/env python3
"""Discover and (optionally) download USGS 3DEP DEM tiles covering every
reach in the DB.

Two modes:

1. ``--emit-manifest PATH``
   Walk every reach with non-NULL geom, compute a per-reach bounding box
   (vertices + ``--pad-deg`` padding), write the list as JSON. Output
   shape::

       {
         "generated_at": "2026-05-22T22:00:00",
         "pad_deg": 0.05,
         "reaches": [
           {"id": 407, "aw_id": 2868, "display_name": "Horse Creek",
            "bbox": [W, S, E, N]},
           ...
         ]
       }

   The manifest is durable; re-emit when the reach set changes.

2. ``--manifest PATH --dataset {1arc3|1m} [--dry-run|--apply]``
   Query the USGS TNM Access API for products in the chosen dataset that
   intersect any reach bbox. Deduplicate by source identifier, summarise.
   ``--dry-run`` (default) just prints; ``--apply`` downloads into
   ``--cache-dir`` (resumes on partial files via HTTP Range).

Cache layout written by ``--apply``::

    DEM-cache/
      1arc3/<sourceID>.tif
      1m/<sourceID>.tif
      manifest.json
      products_<dataset>.json   # cached API response for inspection

Designed for the macOS workstation. Read-only against the DB.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

DEFAULT_DB = os.environ.get("KAYAK_DB", "/Users/pat/tpw/DB/kayak.db")
DEFAULT_CACHE_DIR = Path("DEM-cache")
TNM_PRODUCTS_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"

# TNM Access API "datasets" filter values. Verified against the public TNM
# UI (https://apps.nationalmap.gov/downloader/) and the TNM Access API docs
# linked from there. If TNM renames a dataset, update the mapping; the
# remaining logic is dataset-agnostic.
DATASETS = {
    "1arc3": "National Elevation Dataset (NED) 1/3 arc-second Current",
    "1m": "Digital Elevation Model (DEM) 1 meter",
}


def _parse_linestring(geom: str) -> list[tuple[float, float]]:
    """Parse our raw 'lon lat,lon lat,…' geom into a list of (lon, lat)."""
    out: list[tuple[float, float]] = []
    for pair in geom.split(","):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split()
        if len(parts) != 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        out.append((lon, lat))
    return out


def emit_manifest(db_path: str, pad_deg: float, out_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, aw_id, display_name, geom FROM reach "
        "WHERE geom IS NOT NULL AND geom != '' "
        "ORDER BY id"
    ).fetchall()
    reaches = []
    skipped = 0
    for r in rows:
        verts = _parse_linestring(r["geom"])
        if len(verts) < 2:
            skipped += 1
            continue
        lons = [v[0] for v in verts]
        lats = [v[1] for v in verts]
        bbox = [
            round(min(lons) - pad_deg, 6),
            round(min(lats) - pad_deg, 6),
            round(max(lons) + pad_deg, 6),
            round(max(lats) + pad_deg, 6),
        ]
        reaches.append(
            {
                "id": r["id"],
                "aw_id": r["aw_id"],
                "display_name": r["display_name"],
                "bbox": bbox,
            }
        )

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "pad_deg": pad_deg,
        "reach_count": len(reaches),
        "skipped_no_vertices": skipped,
        "reaches": reaches,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"Wrote {out_path}: {len(reaches)} reaches (skipped {skipped} with <2 parsed vertices)")


def _bboxes_from_manifest(
    manifest_path: str,
) -> list[tuple[int, tuple[float, float, float, float]]]:
    with open(manifest_path) as fh:
        m = json.load(fh)
    return [(r["id"], tuple(r["bbox"])) for r in m["reaches"]]


def _query_tnm(
    client: httpx.Client,
    dataset_label: str,
    bbox: tuple[float, float, float, float],
    max_retries: int = 3,
) -> list[dict]:
    """Fetch TNM products for a single bbox, paginating until exhausted.

    Retries on transient HTTP errors / non-JSON responses (TNM occasionally
    serves a holding page under load).
    """
    items: list[dict] = []
    offset = 0
    while True:
        params = {
            "datasets": dataset_label,
            "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
            "prodFormats": "GeoTIFF",
            "max": 50,
            "offset": offset,
            "outputFormat": "JSON",
        }
        for attempt in range(1, max_retries + 1):
            try:
                r = client.get(TNM_PRODUCTS_URL, params=params, timeout=60.0)
                r.raise_for_status()
                data = r.json()
                break
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                if attempt == max_retries:
                    print(
                        f"    bbox {bbox}: giving up after {max_retries} retries: {exc}",
                        file=sys.stderr,
                    )
                    return items  # partial results from prior pages
                # Backoff: 1, 2, 4 seconds
                import time

                time.sleep(2 ** (attempt - 1))
        page = data.get("items", [])
        items.extend(page)
        total = int(data.get("total", 0))
        offset += len(page)
        if not page or offset >= total:
            break
    return items


def query_dataset(  # noqa: C901  query + summarize + download in one flow; splitting fragments the per-product loop
    manifest_path: str,
    dataset_key: str,
    cache_dir: Path,
    apply: bool,
) -> None:
    if dataset_key not in DATASETS:
        sys.exit(f"unknown dataset: {dataset_key} (try one of {sorted(DATASETS)})")
    label = DATASETS[dataset_key]
    bboxes = _bboxes_from_manifest(manifest_path)
    print(f"Querying TNM for dataset {dataset_key!r} ({label!r})")
    print(f"  Reaches in manifest: {len(bboxes)}")

    products: dict[str, dict] = {}  # sourceID -> item
    product_to_reaches: dict[str, set[int]] = {}

    with httpx.Client() as client:
        for idx, (rid, bbox) in enumerate(bboxes, start=1):
            try:
                items = _query_tnm(client, label, bbox)
            except httpx.HTTPError as exc:
                print(f"  [{idx}/{len(bboxes)}] reach {rid}: HTTP error {exc}", file=sys.stderr)
                continue
            for item in items:
                sid = item.get("sourceId") or item.get("title") or item.get("downloadURL")
                if not sid:
                    continue
                products.setdefault(sid, item)
                product_to_reaches.setdefault(sid, set()).add(rid)
            if idx % 25 == 0:
                print(
                    f"  [{idx}/{len(bboxes)}] reaches queried; unique products so far: {len(products)}"
                )

    print()
    print(f"Total unique products: {len(products)}")
    total_bytes = sum(int(p.get("sizeInBytes") or 0) for p in products.values())
    print(f"Total size: {total_bytes / (1024**3):.2f} GiB ({total_bytes / (1024**2):.0f} MiB)")
    print()

    # Cache the raw response for inspection
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw_path = cache_dir / f"products_{dataset_key}.json"
    with open(raw_path, "w") as fh:
        json.dump(
            {
                "dataset": dataset_key,
                "label": label,
                "products": list(products.values()),
                "product_to_reaches": {k: sorted(v) for k, v in product_to_reaches.items()},
            },
            fh,
            indent=2,
        )
    print(f"Wrote raw product list: {raw_path}")
    print()

    # Print summary table
    sorted_products = sorted(
        products.items(),
        key=lambda kv: int(kv[1].get("sizeInBytes") or 0),
        reverse=True,
    )
    print(f"{'sourceId':<50}  {'MiB':>8}  {'#reaches':>9}  title")
    print("-" * 120)
    for sid, item in sorted_products[:40]:
        size_mb = (int(item.get("sizeInBytes") or 0)) / (1024**2)
        nreach = len(product_to_reaches.get(sid, ()))
        title = (item.get("title") or "")[:60]
        print(f"{sid[:50]:<50}  {size_mb:>8.1f}  {nreach:>9}  {title}")
    if len(sorted_products) > 40:
        print(f"... and {len(sorted_products) - 40} more (see {raw_path})")

    if not apply:
        print()
        print(
            f"Dry-run only. Pass --apply to download {total_bytes / (1024**3):.2f} GiB into {cache_dir / dataset_key}/"
        )
        return

    # Apply: download each product with HTTP Range resume
    out_dir = cache_dir / dataset_key
    out_dir.mkdir(parents=True, exist_ok=True)
    print()
    print(f"Downloading {len(products)} files into {out_dir}/ ...")
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        for idx, (_sid, item) in enumerate(sorted_products, start=1):
            url = item.get("downloadURL")
            if not url:
                continue
            fname = url.rsplit("/", 1)[-1]
            dst = out_dir / fname
            expected = int(item.get("sizeInBytes") or 0)
            if dst.exists() and expected and dst.stat().st_size == expected:
                continue
            start = dst.stat().st_size if dst.exists() else 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            mode = "ab" if start else "wb"
            try:
                with client.stream("GET", url, headers=headers) as r:
                    r.raise_for_status()
                    with open(dst, mode) as fh:
                        for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                            fh.write(chunk)
                print(
                    f"  [{idx}/{len(products)}] {fname} ({dst.stat().st_size / (1024**2):.1f} MiB)"
                )
            except httpx.HTTPError as exc:
                print(f"  [{idx}/{len(products)}] FAILED {fname}: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite DB path (default: {DEFAULT_DB})")
    ap.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        type=Path,
        help=f"DEM cache root (default: {DEFAULT_CACHE_DIR})",
    )
    ap.add_argument(
        "--pad-deg", type=float, default=0.05, help="Bbox padding in degrees (default: 0.05)"
    )
    ap.add_argument(
        "--emit-manifest", default=None, help="Compute per-reach bboxes and write to this path"
    )
    ap.add_argument("--manifest", default=None, help="Read this manifest for dataset queries")
    ap.add_argument(
        "--dataset", choices=sorted(DATASETS), default=None, help="Dataset to query/download"
    )
    ap.add_argument("--apply", action="store_true", help="Actually download (default is dry-run)")
    args = ap.parse_args()

    if args.emit_manifest:
        emit_manifest(args.db, args.pad_deg, args.emit_manifest)
        return 0

    if args.manifest and args.dataset:
        query_dataset(args.manifest, args.dataset, args.cache_dir, args.apply)
        return 0

    ap.error("must pass either --emit-manifest, or --manifest + --dataset")


if __name__ == "__main__":
    sys.exit(main())
