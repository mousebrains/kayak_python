# PLAN: Metadata as a single source of truth (symbolic-FK CSVs)

**Status:** In progress (design v1). **Supersedes** the later phases of
`project-review-6/PLAN_round6_remediation.md` — the round-6 *review* stands (it surfaced the root cause);
this is the strategic remediation the maintainer chose over the duality-based plan.

## Motivation — why round 6 kept generating complexity

Six red-team passes on the round-6 remediation kept circling one root cause: **metadata lives in two places
at once.** A change is written as a SQL *data migration* (which mutates the long-lived prod DB) *and* later
captured in the `data/db/*.csv` snapshot (the rebuild source). The two are bridged by an id only prod can
assign — `source.id`/`gauge.id` are autoincrement PKs, so you can't author a new row in the CSV (you don't
know its id), so you write a migration to mint it, then wait for the snapshot to back-fill the CSV. That one
fact is the engine behind `PENDING_RECONCILIATION`, the reconciliation guard, the gauge-217 migration-vs-CSV
dual-edit, and the whole class of drift the review found.

**Key realisation (maintainer):** the CSVs *are* the definitive metadata, and **observations are
re-harvestable from their live sources** — so we only need to recreate the *metadata*, then let the pipeline
re-populate data. That removes the need to preserve numeric ids at all.

## Target architecture (six principles)

1. **Metadata = one CSV per table, with symbolic (natural-key string) foreign keys** — no numeric `id`
   columns in the CSVs. `gauge_source.csv` carries `(gauge_name, source_key)`, not `(gauge_id, source_id)`.
2. **Three identifiers per row, cleanly separated.** `name` = a unique, human-readable **symbolic-FK key**
   (what other CSVs reference the row by — readable when hand-editing); an immutable, opaque **`hash`** column
   (`[a-z0-9]+`) on the bookmarkable tables (`source`/`gauge`/`reach`) = the **public URL handle**;
   `display_name` = presentation (what the user sees). The numeric `id` is a **pure ephemeral internal
   surrogate**, reassigned freely on every rebuild.
3. **Public URLs and custom pages reference the immutable `hash`, never the row `id`** — so bookmarks and
   custom-gauge lists survive both a rebuild *and* a later `name` rename (the `hash` decouples the public id
   from both the ephemeral id and the mutable name). A row is looked up by its `hash` — a unique indexed
   column, so no separate hash→row table is needed. The constraint forcing this: row ids are baked into `?id=`
   URLs and the `custom_gauges` `ids=` list today. We switch **now**, early, while few bookmarks exist.
4. **Observations are not in the CSVs** — a rebuild recreates metadata and the pipeline re-fetches data.
5. **Migrations become schema-only** (ALTER/CREATE/DROP — the one thing CSVs can't express). No more data
   migrations; a metadata change is a reviewed **CSV diff**.
6. **The CSVs are the single source of truth, living in the data repo** (this subsumes the round-6 data-repo
   split — the data repo *is* the authoritative metadata, reviewed via PRs).

## Per-table natural key (the symbolic id)

The 15 metadata tables form a clean DAG (no cycles); `import_metadata`'s `LOAD_ORDER` already topo-sorts.

| Table | Proposed `key` | Notes |
|---|---|---|
| `state` | `name` | already unique |
| `class_description` | `name` | already the PK |
| `huc_name` | `huc` | already natural |
| `fetch_url` | `url` | already unique |
| `gauge` | `name` | already unique; **+ `hash`** public handle |
| `source` | `name` | **make unique** — only 2 collisions to fix (29C100×3, 28B080×3, all WA DOE); **+ `hash`** |
| `reach` | `name` (`aw_####`) | unique-where-non-NULL; **34/420 lack a name** → assign (33 `aw_<aw_id>`, 1 hand); **+ `hash`** |
| `guidebook` | `(title, edition)` | or a short slug |
| `calc_expression` | new required+unique `slug` | stores `data_type`, not the target; `provenance_slug` is NULL for operational calcs |
| `rating` | `url` | dormant; low priority |
| `gauge_source` | `(gauge.name, source.name)` | junction |
| `reach_state` | `(reach.name, state.name)` | junction |
| `reach_class` | `(reach.name, name)` | `name` = the class grade |
| `reach_guidebook` | `(reach.name, guidebook key)` | junction + extra columns |
| `rating_data` | `(rating.url, gauge_height_ft)` | junction |

**One-time normalization (the only manual data work):** disambiguate the 6 WA DOE source rows so `name` is
unique; assign names to the 34 nameless reaches; add a `slug` to each `calc_expression`; generate an immutable
`hash` for every existing `source`/`gauge`/`reach`. After this, the CSVs are the source of truth and changes
are plain diffs.

## Rebuild algorithm

```
for table in topo_order(metadata_tables):       # state, fetch_url, …, gauge, source, gauge_source, reach, …
    for row in read_csv(table):                 # CSV has key + data cols + symbolic FK cols, NO id
        resolve each symbolic FK col against keymap[parent_table][fk_value]   # → numeric parent id
        new_id = INSERT row (DB assigns the autoincrement id)
        keymap[table][row.key] = new_id
```

`observation` and the `latest_*` caches are **not** loaded — the pipeline fetches them after the rebuild.
This is what `import_metadata` already does (topo order + per-table load); the change is: read the symbolic
`key`/FK columns instead of numeric ids, and maintain the `keymap` to resolve FKs.

## Bookmark / custom-page migration (the user-facing half)

- **Detail pages:** `reach.php?id=` / `gauge.php?id=` / `source.php?id=` → resolve by `key`
  (`?r=<reach.name>`, `?g=<gauge.name>`). Keep a transitional `?id=` → 301-to-`?key=` redirect *only* until
  the first rebuild (after which old numeric ids are meaningless); accept that pre-existing `?id=` bookmarks
  break once, by design ("now, early in the lifecycle").
- **Custom pages:** `custom_gauges_handler.php` encodes the user's list as `ids=<csv of row ids>`
  (`:7,82,220`). Switch the encoding to `keys=<csv of gauge.name>`. This is the load-bearing one — it's the
  thing that must survive rebuilds.

## Prod application

With re-harvestable observations, the simplest model is **rebuild-and-swap** (the existing `db_push.sh`
pattern): build a fresh metadata DB from the CSVs into a temp file, run the pipeline to populate it, then
atomically swap it in. No id preservation, no in-place sync. (An id-preserving incremental upsert is possible
but unnecessary — and is exactly the complexity we're removing.)

## What this dissolves (round-6 mapping)

- **gauge 217** (review §1 #2) → a one-line `gauge.csv` `sort_name` edit, reviewed. No migration 0072, no
  dual-edit, no rebuild-staleness.
- **`seed_gauge_display` clobber** → the tool becomes a CSV-*generation* helper; its output is a reviewable
  diff, not a prod-DB mutation. (Its fill-only/`--gauge` safety is moot once it edits the reviewed CSV.)
- **`PENDING_RECONCILIATION` / the reconciliation guard / the dual-edit** → gone (no id chicken-and-egg, one
  source of truth).
- **The data-repo split / branch protection (review §1 #1)** → still wanted, and now the data repo simply
  holds the authoritative symbolic-FK CSVs.
- **CHANGELOG / 0069 header** (review §3) → fold in as part of the redesign PRs.

## Migration path (phased, to be detailed)

1. **Introduce keys + uniqueness.** Make `name` unique on `source` (resolve any collisions); assign keys to
   NULL-name reaches; add the `calc_expression` slug. Schema migration(s) — the *last* data-touching ones.
2. **Switch the public surface to keys.** PHP detail-page + custom-page URLs use `key`; transitional id
   redirect.
3. **Convert the CSVs to symbolic-FK form** (drop `id` columns, rewrite FK columns as key refs) and **rewrite
   the loader** to resolve symbolically + maintain the keymap. Add a round-trip test (export→import→export is
   stable).
4. **Retire data migrations** — `levels migrate` handles schema only; document the new "edit the CSV" flow.
5. **Move the CSVs to the data repo** (the round-6 split) + branch-protect the code repo.

## Decisions (resolved with the maintainer)

1. **`source` key** — make `name` **unique** (disambiguate the 6 WA DOE collision rows). ✓
2. **Public handle** — a separate **immutable `hash` column** on `source`/`gauge`/`reach` (not the mutable
   `name`), looked up directly (no hash→row table). ✓
3. **Reaches** — all should have a `name`; assign the 34 missing ones (33 → `aw_<aw_id>`, 1 hand-assigned). ✓
4. **`calc_expression`** — a hand-assigned required+unique `slug`. ✓

## Remaining small choices (low-stakes, settle during phase 1)

- **`hash` format** — e.g. 7-char base32 `[a-z0-9]`, collision-checked at generation. (Or reuse the
  user's prior scheme.)
- **The 6 source disambiguations — resolved (maintainer):** change the **`wa.gov` parser to name sources by
  the URL filename stem** instead of the bare station id, so each parameter feed is uniquely named:
  `…/28B080/28B080_WTM_FM.TXT` → `28B080_WTM_FM` (STG/DSG/WTM = stage/discharge/water-temp). This generalizes
  to *all* wa.gov sources, not just the colliding `29C100`/`28B080` triplets — they become consistently
  filename-named, and `UNIQUE(source.name)` then holds with no hand-suffixing.
- **Reach id 304** — has neither `name` nor `aw_id` ("SF McKenzie"); pick a hand key.
- **Transitional `?id=` redirect** — keep until the first rebuild, then drop.
