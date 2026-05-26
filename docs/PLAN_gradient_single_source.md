# Plan: `gradient_profile` single source of truth (review-3 R6.1)

## Problem
`reach.gradient_profile` (the per-reach sample JSON, ~1.6 MB / ~83 % of
`reach.csv`) lived in **three** places — `reach.csv`, migration `0046` (the
immutable backfill), and `0059` (an 11-row repair) — so any gradient recompute
had to touch all three or they silently diverged (REVIEW §2). Only
`gradient_profile` is the bloat; `max_gradient` is one float and stays put.

## Design — mirror the proven geom → `reaches.json` snapshot
`reach.geom` already solves the identical problem: excluded from `reach.csv`,
snapshotted to `data/db/reaches.json`, applied by `import_metadata.py`. Do the
same for `gradient_profile`:

- **`export_metadata.py`** — add `"gradient_profile"` to
  `EXCLUDED_COLUMNS["reach"]`; add `write_reaches_gradient_json()` →
  `data/db/reaches-gradient.json` keyed by reach id (numeric order).
  `max_gradient` stays in `reach.csv`.
- **`import_metadata.py`** — add `_apply_gradient()` (parallel to `_apply_geom`):
  `UPDATE reach SET gradient_profile = ? WHERE id = ?`; call it from `main`
  after `_apply_geom`; add a `--gradient-only` flag (parallel to `--geom-only`).
  The PK-upsert preserves columns the CSV omits, so dropping `gradient_profile`
  from `reach.csv` is safe (a reach carrying gradient in the live DB but absent
  from the snapshot keeps it on a full import).
- **`scripts/deploy.sh`** — extend the `reaches.json`-changed gate to also apply
  `reaches-gradient.json` when it changes (`--gradient-only`).
- **`.gitattributes`** — `data/db/reaches-gradient.json -diff linguist-generated`.
- **Migrations `0046` / `0059`** — left as immutable history: they seed gradient
  on a from-0001 migrate, exactly as geom is seeded by its migration;
  `reaches-gradient.json` is the going-forward source.

## Blast radius
PHP/build read gradient from the **DB**, not the CSV (`reach_detail.php`,
`svg_plot.php`, `reach_fields.php`) — untouched; the `/reach.php` chart is
byte-identical.

## Verify
Round-trip test (export → fresh-DB import reproduces gradient via the JSON;
`gradient_profile` excluded from `reach.csv`; `--gradient-only` applies only
gradient; gradient absent from the snapshot survives a full import);
`reach.csv` shrinks ~1.6 MB; re-export diff is `reach.csv` + the new
`reaches-gradient.json` only.

## Status
Implemented in this PR. The mechanism is documented inline parallel to geom
(CLAUDE.md § schema-evolution, `docs/migrations.md` rebuild runbook).
