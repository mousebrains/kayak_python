# Plan: full code/data separation (first pass)

Goal: make `kayak_python` a region-agnostic engine so another club — the
running example is Tennessee — can deploy it on **their own VM** with
**their own dataset repo**. One deployment = code@version + a dataset
clone + a handful of env keys. The acceptance test: a TN deployer
clones the code, instantiates a dataset skeleton, harvests their
region, and goes live **without editing any file in the code repo**.

Decisions already made (2026-06-05):

- **sources.yaml is the human-edited master** and moves to
  `kayak_data`; the `source`/`fetch_url` rows (CSV + DB) are
  *generated* from it.
- **Regression artifacts belong to the dataset** (`kayak_data`).
- **`site.yaml`** carries site identity, with **full prose-page
  templating** (about/disclaimer/etc. content lives in the dataset).

Status: second pass (2026-06-05, post-#122/#4 merge) — the #122 review
cycle already landed a slice of S4 (see that section). To be iterated
once more with the remaining open questions answered, then S4 starts.
Sequencing intent: S4 → S1 → S2 → S6 → S3 → S5 (contract and
testability first, cosmetics later).

## What is already clean

The Phase 5/6 metadata redesign did the heavy lifting: metadata CSVs +
`reaches.json` + `reaches-gradient.json` live in `kayak_data`
(`METADATA_DIR`), applied by `levels sync-metadata` and snapshotted
nightly; schema + migrations are code-side; PHP reads a runtime-config
snapshot. The calculator, USGS-OGC fetch, parsers, build, editor
system, HUC/trace tooling, and pubhash are already region-agnostic
mechanisms.

## Gap inventory (verified against the tree, 2026-06-05)

| # | Coupling | Where |
|---|---|---|
| G1 | Fetch registry is code-repo data | `data/sources.yaml` (122 URLs, parser-grouped, per-station TZs); near-duplicate of `fetch_url.csv` (`url,parser,hours,is_active`) + `source.timezone` |
| G2 | Published analysis content in code repo | `docs/regression/*.{md,svg,json}` → `/static/regression/`; `calc_expression.provenance_slug` points across repos |
| G3 | Branding hardcoded | `levels.wkcc.org` / WKCC in `src/kayak/web/build/{deploy,shell,_shared,gauges}.py` and `php/{login,disclaimer,privacy,contact}.php`; canonical URLs; map default center |
| G4 | Tests/CI require the private dataset | ≥6 test files read `METADATA_DIR` (`test_committed_reach_geom` count guard, `test_id_counters`, `test_reach_names`, `test_schema_doc_sync`, …); CI checks out private `kayak_data` |
| G5 | Region-flavored tooling | PNW season buckets in `gauge_pair_linear.py` reports; PST/PDT table in `gauge_lead_lag.py`; WA-Ecology/dreamflows harvesters; Trace-cache/DEM-cache are regional downloads |
| G6 | Deploy scaffolding names | `deploy.sh`, `snapshot_metadata.sh`, systemd units, `deploy/SETUP.md` carry wkcc/mousebrains specifics |

Reproduce the inventory:

```bash
grep -rln "wkcc\|levels.wkcc.org\|Willamette" src/kayak/web/ php/ --include='*.py' --include='*.php'
grep -rln "METADATA_DIR" tests/
head -30 data/sources.yaml; head -1 <kayak_data>/fetch_url.csv
```

## S1 — sources.yaml as the master fetch registry, in the dataset

End state: `kayak_data/sources.yaml` is the only thing a human edits to
add/retire a fetch source. The `source` + `fetch_url` CSV rows (and
therefore the DB rows, via the unchanged `sync-metadata` path) are
**generated** from it.

- New code-side generator: `levels generate-sources --dataset <dir>`
  reads `sources.yaml`, validates every parser name against the
  registry (`ensure_all_loaded()`), assigns/preserves stable ids from
  `id_counters.csv`, and rewrites `source.csv` + `fetch_url.csv`
  (timezones land in `source.timezone` exactly as today).
- Editing flow: edit YAML → run generator → commit YAML + CSVs in one
  `kayak_data` PR. The dataset's CI (see S4) re-runs the generator and
  fails on drift, so the CSVs can never diverge from the YAML.
- `levels fetch` keeps reading the DB (no behavior change at run
  time); `init-db --no-seed` becomes the default and the YAML-seeding
  path is deleted (a fresh install = init-db + import from dataset).
- `_auto_create_source` (parsers/base.py) survives but its created
  rows now flow back through the snapshot like any prod-side edit.
- Sweep: orphan-check prose, `snapshot_metadata.sh`, CLAUDE.md,
  `docs/migrations.md`, deploy.sh step list.

Open detail: whether the generator preserves manual `is_active=0`
overrides in the CSV or the YAML grows an `enabled:` per URL
(recommended: YAML carries it — single master).

## S2 — regression content moves to the dataset

- `git mv` the published artifacts (`*.md`, `*.svg`, `*.json`,
  `*_leadlag.*`) from `docs/regression/` → `kayak_data/regression/`;
  the tools (`scripts/regression/*`) and their READMEs stay code-side.
- `deploy.py::_deploy_regression_artifacts` reads
  `METADATA_DIR/regression/`; the report generators' `--out` defaults
  follow.
- `provenance_slug` then resolves entirely within the dataset (a TN
  calc's provenance doc ships with TN's data).
- The `docs/regression/README.md` workflow/index splits: tool docs stay
  in the code repo; the per-fit index becomes
  `kayak_data/regression/README.md`.

## S3 — site identity + prose as dataset content (`site.yaml` + pages)

- `kayak_data/site.yaml`: site name, canonical base URL, organization,
  contact email, map default center/zoom (or `auto` = bbox of reach
  extent), analytics key, footer links, guidebook plug, RFC/agency
  link labels if any prove region-specific.
- Plumbed through the existing `levels emit-config` snapshot so PHP
  reads it exactly the way it reads `database_path`; the Python build
  reads it directly. Sweep the G3 file list; no hardcoded
  `levels.wkcc.org` anywhere afterward.
- **Full prose-page templating**: `kayak_data/site/` carries Markdown
  fragments (`about.md`, `disclaimer.md`, `privacy.md`,
  `contact.md`, …). The build renders them into the existing page
  shells (layout, nav, and forms remain code-side; CSP constraints
  unchanged — rendered fragments contain no scripts). A missing
  fragment renders a minimal generic page, so a new dataset is usable
  before its prose is written.

## S4 — decouple tests/CI from the private dataset (keystone)

Without this, no outside deployer can run the suite at all.

**Head start — already landed via the #122 review cycle (merged
2026-06-05):** the `EXPECTED_REACH_COUNT` constant is retired
(`test_committed_reach_geom` derives the expected count from the
dataset's own `reach.csv` and enforces cross-set integrity — JSON
snapshot keys and child-CSV `reach_id`s must be subsets of
`reach.csv`'s ids, every reach must carry geometry), and CI tests a
**same-named kayak_data branch** when one exists (paired-PR support).
Metadata-only reach changes already need no code commit.

Remaining S4 scope:

- **Fixture dataset in the code repo** (`tests/fixtures/dataset/`):
  two states, three gauges (one USGS, one NWS, one calc), one
  calc_expression, two reaches with stub geometry, a minimal
  `sources.yaml` + `site.yaml`. Unit/integration tests default to it;
  `METADATA_DIR` becomes a test override rather than a requirement.
- **Dataset guards move to the dataset's CI** via a published
  `levels validate-dataset <dir>` command: id-counter rules, CSV
  shape, geom format + endpoint checks and the derived-count +
  cross-set-integrity invariants (today in
  `test_committed_reach_geom`), reach-name rules, YAML↔CSV generator
  drift (S1). `kayak_data`'s CI calls it; so does Tennessee's.
- Code-repo CI drops the private checkout from the required test jobs;
  an **optional integration job** (continues to use the paired-branch
  checkout logic) runs when the secret is present, for our own
  deploys.

## S5 — bootstrap path for a new region

Two ways to hand a new club a starting dataset; recommendation is (b),
optionally publishing (a) generated from it:

- (a) **GitHub template repository** — a repo flagged "Template" so
  "Use this template" stamps out a fresh copy (no history). Zero code,
  but a second artifact to keep in sync.
- (b) **`levels init-dataset <dir>`** — the code scaffolds the
  skeleton (empty CSVs with headers, `id_counters` at 1,
  `site.yaml.example`, `sources.yaml` stub, `regression/`, `site/`,
  `validate` wiring, README). Always matches the installed code's
  schema version; nothing extra to maintain.
- Region runbook (docs): harvest AW reaches by state, USGS state
  catalog, the region's RFC + NWPS lids, state agencies, Trace-cache
  NHD HUC4s + DEM tiles + OSM extract for traces/gradients. TN
  specifics worth noting: HUC4s 0601–0604, SERFC/LMRFC/OHRFC instead
  of NWRFC, and a **TVA parser** would be the first genuinely new
  engine code — parsers remain code-side plugins referenced by name
  from the dataset's `sources.yaml`.
- G5 cleanups ride along: the regression tools' season buckets and the
  lead-lag TZ table become dataset/config-driven (site.yaml keys, e.g.
  `hydrologic_seasons`, `timezone`).

## S6 — versioned dataset contract

- `kayak_data/dataset.yaml`: `schema_version` (the CSV/JSON shape
  contract), `min_code_version`, row-count guards (S4), generated-at.
- `sync-metadata`, `import_metadata`, and `validate-dataset` check it
  and refuse loudly on mismatch — the cross-repo analogue of
  `schema_migrations`. A CSV-shape change in code bumps the supported
  version and documents the dataset migration step.

## Sequencing, effort, risk

| Phase | Size | Risk notes |
|---|---|---|
| S4 fixtures + validate-dataset | M | test surgery; biggest payoff; do first |
| S1 sources.yaml master | M | touches fetch boot/init-db/snapshot; the generator must preserve stable ids |
| S2 regression move | S | mechanical; one-time `git mv` + path changes |
| S6 dataset.yaml contract | S | small, do before S3/S5 consume it |
| S3 site.yaml + prose templating | M–L | PHP/build sweep + fragment rendering under CSP |
| S5 init-dataset + runbook | S–M | mostly docs + scaffold command |

Cross-cutting: every phase lands as a paired PR (code + kayak_data)
using the paired-branch CI checkout from #122; deploy ordering rules
unchanged (code first, prod sync before the nightly snapshot).

## Open questions (for the next iteration of this plan)

1. S1: `enabled:` flags in YAML vs preserving CSV `is_active`
   overrides — confirm YAML-only as the master switch.
2. S2: do historical Soggy-Sneakers-era report SQL stubs move verbatim
   (recommendation: yes, immutable history travels with the dataset)?
3. S4: how small can the fixture geometry be while keeping
   check-reaches meaningful (stub 5-point linestrings vs one real
   small reach)?
4. S3: which prose pages are dataset content vs engine pages
   (proposed: about/disclaimer/privacy/contact prose = dataset;
   login/admin/editor chrome = engine).
5. S6: version the *dataset contract* independently of the package
   version, or pin to it?
