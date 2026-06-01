# PLAN: Metadata as a single source of truth

**Status:** In progress (design **v2**) — **Phases 1–5 landed** (#100 / #103 / #104 / #105 + the
schema-only-migration retirement); **Phase 6 (data-repo split) remains**. **Supersedes** the later phases of
`project-review-6/PLAN_round6_remediation.md` — the round-6 *review* stands (it surfaced the root cause);
this is the strategic remediation the maintainer chose over the duality-based plan.

> **v2 simplification (maintainer).** The numeric `id` stays as the **stable, author-assigned** key (not an
> ephemeral surrogate), so the public URL handle is simply **`base62(id)`** — `decode → id → WHERE id = ?`,
> with **no separate `hash` column and no lookup table**. New ids come from a **monotonic per-type counter**
> (only ever increments → a deleted id is never reused → a base-62 handle never silently re-points). Because
> the id is stable, **foreign keys stay numeric** — so the symbolic-FK CSV conversion + loader rewrite (the v1
> Phase 3) are **dropped**, and `observation` int FKs stay valid across rebuilds. `name` (made unique in
> Phase 1a/1b) remains a human-readable handle/lookup; `display_name` is presentation. The architecture below
> is restated for v2; the per-table-key + phase sections further down still describe the superseded v1
> (symbolic-FK) shape and are being realigned.

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

## Target architecture (v2)

1. **One CSV per table, keeping the numeric `id` column** — but the id is **author-assigned and stable**, not
   a prod autoincrement, and never reassigned. **Foreign keys stay numeric** (`gauge_source.csv` =
   `gauge_id, source_id`), so a rebuild loads rows with their explicit ids and the `observation` int FK stays
   valid — no symbolic-FK resolution, no key→id loader.
2. **New ids come from a monotonic per-type counter** (`data/db/id_counters.csv`: `source`/`gauge`/`reach`
   high-water marks). A new row takes `counter + 1` and bumps the counter; the counter **only increments**, so
   a deleted id is **never reused**. A CI guard enforces: ids unique per type, and every id ≤ its counter.
3. **The public URL handle is `base62(id)`** — computed, not stored (`decode(handle) → id → WHERE id = ?`); no
   `hash` column, no hash→row table. Base-62 `[0-9a-zA-Z]` (case-sensitive — URL query strings and SQLite's
   `BINARY` collation both preserve case), 1-based so the falsy `"0"` never appears. The id is stable, so the
   handle survives rebuilds and is decoupled from the mutable `name` (renames don't change it). URLs + custom
   pages switch from `?id=<decimal>` to the base-62 handle; per-type pages keep per-type id spaces (per-table).
4. **Observations are not in the CSVs.** A from-scratch rebuild re-fetches them; an incremental metadata
   change *preserves* them (the id is stable, so the FK stays valid).
5. **Migrations become schema-only** — a metadata change is a reviewed **CSV diff**, applied to prod by the
   incremental sync (matching by id, preserving observations).
6. **The CSVs are the single source of truth, in the data repo** (subsumes the round-6 data-repo split).

`name` (unique after Phase 1a/1b) stays a human-readable handle for lookup and CSV reference; `display_name`
is presentation; the stable `id` is the PK, the FK target, and — base-62 encoded — the public handle. One
identifier does the work the v1 design split across `id` + `hash` + `name`.

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
- **`PENDING_RECONCILIATION` / the reconciliation guard / the dual-edit** → **gone** (Phase 5): the
  `test_migration_csv_reconciliation` guard is replaced by `test_migrations_schema_only` (new migrations may
  not touch metadata tables); no id chicken-and-egg, one source of truth.
- **The data-repo split / branch protection (review §1 #1)** → still wanted, and now the data repo simply
  holds the authoritative symbolic-FK CSVs.
- **CHANGELOG / 0069 header** (review §3) → fold in as part of the redesign PRs.

## Migration path (phased) — v2 actual

The v1 list here described the symbolic-FK shape (the old Phase 3) that **v2
dropped** — ids stay numeric and stable, FKs stay numeric. The sequence as actually
executed:

1. **✓ Phase 1 — normalization + stable-id foundation** (#100): `id_counters.csv`,
   the `test_id_counters` guard, the stable author-assigned id.
2. **✓ Phase 2 — base-62 `?h=` public handles** (#103) + **drop internal-id columns
   from public pages** (#104). `base62(id)`, no `hash` column, no lookup table.
3. **✓ Prod-apply sync** (#105): `levels sync-metadata` applies a reviewed CSV diff
   to the live DB by stable id, preserving observations; wired into `deploy.sh`
   step 3.1.
4. **✓ Phase 5 — retire data migrations**: `levels migrate` is schema-only
   (`test_migrations_schema_only` replaces `test_migration_csv_reconciliation` +
   `PENDING_RECONCILIATION`); the metadata-edit flow is documented in
   `docs/PLAN_add_gauges_reaches.md` (add / update / split / drop via CSV + sync),
   `docs/migrations.md`, and `CLAUDE.md`.
5. **☐ Phase 6 — data-repo split**: move `data/db/*.csv` to the data repo +
   branch-protect the code repo (the round-6 split).

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
