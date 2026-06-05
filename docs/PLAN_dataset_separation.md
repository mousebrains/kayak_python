# Plan: full code/data separation

Goal: make `kayak_python` a region-agnostic engine so another club — the
running example is Tennessee — can deploy it on **their own VM** with
**their own dataset repo**. One deployment = code@version + a dataset
clone + a handful of env keys. The acceptance test: a TN deployer
clones the code, instantiates a dataset skeleton, harvests their
region, and goes live **without editing any file in the code repo**.

Decisions (2026-06-05, all settled):

- **sources.yaml is the human-edited master** and moves to
  `kayak_data`; the `source`/`fetch_url` rows (CSV + DB) are
  *generated* from it. Per-URL `enabled:` flags live in the YAML —
  the single master; the generator never preserves manual CSV
  overrides.
- **Regression artifacts belong to the dataset** (`kayak_data`),
  historical SQL-stub-era reports moving verbatim (immutable history
  travels with the dataset).
- **`site.yaml`** carries site identity, with **full prose-page
  templating**: about/disclaimer/privacy/contact *prose* is dataset
  content; login/admin/editor chrome stays engine-side.
- **Fixture dataset uses some real reaches** (not stubs) — see S4.
- **Scaffolding via `levels init-dataset <dir>`** (no separate
  template repo to maintain).
- **The dataset contract is versioned independently** of the package
  version (S6).
- **Target CI architecture (the inversion):** the code repo's CI is
  **completely standalone** — no kayak_data checkout of any kind; the
  **data repo's CI checks out the code repo's `main`** and runs the
  dataset validations with it. Cross-repo integration testing lives
  data-side, where the data is.

Status: third pass (2026-06-05) — all open questions resolved; the
#122 review cycle already landed a slice of S4 (see that section).
Implementation starts with S4. Sequencing: S4a → S4b → S1 → S2 → S6 →
S3 → S5 (contract and testability first, cosmetics later).

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
| G3 | Branding hardcoded | `levels.wkcc.org` / WKCC in `src/kayak/web/build/{deploy,shell,_shared,gauges}.py`, `php/{about,login,disclaimer,privacy,contact,comment,status}.php`, `php/includes/header.php` (og:site_name + host fallback), `php/includes/mail.php` (noreply@ + email body branding), `src/kayak/config.py` (`site_url` default), `src/kayak/analytics/monitors.py` (User-Agent), `php/includes/propose_handler.php` (a functional `Config::str('site_url', 'https://levels.wkcc.org')` fallback); the map default center is a **constant in static JS** (`static/map.js` `DEFAULT_VIEW`) and `shell.py` hardcodes per-state Windy links for six states |
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
  `kayak_data` PR. The dataset's CI re-runs the generator and fails on
  drift, so the CSVs can never diverge from the YAML (this drift check
  lands as part of S1, extending the S4a `validate-dataset` command).
- **Runtime behavior MUST change — this is the load-bearing part.**
  Today `levels fetch` calls `sync_sources()` on *every run*, which
  reads `load_sources()` → the code repo's `data/sources.yaml` via
  `config_data._DATA_DIR` (fetch.py:150, load_sources at :143; init_db.py's
  `notin_(yaml_urls)` branch deactivates any fetch_url absent from the
  YAML). Moving the YAML without retiring that path would deactivate
  every fetch_url on the first prod fetch. S1 therefore **deletes the
  runtime YAML sync entirely**: after S1, `levels fetch` reads only DB
  rows, which arrive exclusively via the generated CSVs +
  `sync-metadata`. `config_data.load_sources` and the sources-YAML
  branch of `config_data` go away with it — but **`_DATA_DIR` itself
  stays**: it also backs `builder.yaml`, `descriptions.yaml`, and
  `http_concurrency.yaml`, which are genuinely code-side engine config
  (the build reads builder.yaml every run; the HTTP client reads the
  concurrency overrides every fetch). Scope the deletion to
  `load_sources`/`sync_sources` only.
- `init-db` stops seeding from the YAML **and stops seeding the
  hardcoded 12-state list** (`_seed_states()` — a PNW-flavored
  coupling the original gap inventory missed; states come from the
  dataset's `state.csv` like every other metadata row). `--no-seed`
  semantics become the only behavior; a fresh install = `init-db` +
  dataset import.
- `_auto_create_source` (parsers/base.py) survives but its created
  rows now flow back through the snapshot like any prod-side edit.
- Sweep: orphan-check prose, `snapshot_metadata.sh`, CLAUDE.md,
  `docs/migrations.md`, deploy.sh step list, and
  `scripts/import_metadata.py`'s docstring (it documents the
  init-db-seeds-then-reimport dance S1 retires).

Resolved: the YAML carries per-URL `enabled:` flags — the single
master. The generator writes `is_active` from them; manual CSV edits
to generated columns are overwritten on the next generation (and the
drift check makes them un-commitable anyway).

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
- `_deploy_regression_artifacts` currently **returns silently** when
  the source dir is missing — after the move it must distinguish "the
  dataset has no regression docs" (legitimate: a fresh dataset; warn
  and continue) from a misconfigured path, and log either way so an
  empty `/static/regression/` is never a silent surprise.
- Prose-ref sweep rides along: `ci.yml`'s pip-audit markdown-CVE
  suppression rationale and `deploy.py`'s "the kayak repo is private"
  comment both point at the code-repo path S2 removes.

## S3 — site identity + prose as dataset content (`site.yaml` + pages)

- `kayak_data/site.yaml`: site name, canonical base URL, organization,
  contact email, map default center/zoom (or `auto` = bbox of reach
  extent), analytics key, footer links, guidebook plug, RFC/agency
  link labels if any prove region-specific.
- Plumbed through the existing `levels emit-config` snapshot so PHP
  reads it exactly the way it reads `database_path`; the Python build
  reads it directly. The sweep must be **grep-driven, not list-driven**
  (`grep -ri 'wkcc\|levels\.wkcc\.org\|willamette' src/ php/ static/
  public_html/ systemd/ deploy/` as the acceptance check) — the G3
  list is a starting inventory, and it has already grown twice under
  review; afterward no hardcoded `levels.wkcc.org` anywhere, including
  `Config::str(...)` fallback defaults.
- **Full prose-page templating**: `kayak_data/site/` carries Markdown
  fragments (`about.md`, `disclaimer.md`, `privacy.md`,
  `contact.md`, …). The build renders them into the existing page
  shells (layout, nav, and forms remain code-side; CSP constraints
  unchanged — rendered fragments contain no scripts). A missing
  fragment renders a minimal generic page, so a new dataset is usable
  before its prose is written.
- **Static-JS values need a build-time injection mechanism** — config
  snapshots are invisible to static assets. The map center lives in
  `static/map.js` (`DEFAULT_VIEW` constant) and   per-state Windy links with hardcoded OR/ID coordinates. The build
  must inject these (e.g. emit a tiny `site-config.js` the pages load
  first, or rewrite the constants while copying static/ — pick one
  mechanism and use it for every static-side value). Email identity
  (`mail.php`) and `config.py`'s `site_url` default route through the
  same `site.yaml` keys.

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

Remaining S4 scope, in two PR-sized stages:

**S4a — fixture dataset + `validate-dataset`:**

- **Fixture dataset in the code repo** (`tests/fixtures/dataset/`):
  two states, three gauges (one USGS, one NWS, one calc), one
  calc_expression, **two or three real reaches** — selected for
  public-domain-safe provenance: NHD-traced geometry (USGS public
  domain) and **`aw_id`-NULL, club-authored attribute text**. The
  dataset has ~29 such rows (e.g. UMNRO, id 38) — enumerate them with
  a real CSV parser (`csv.DictReader`, NOT awk: quoted multi-line
  description fields misalign naive column splits — a first draft of
  this plan named HOTI/Drift as exemplars on exactly that error; both
  carry aw_ids). Verify each candidate's provenance before inclusion;
  the McKenzie split reaches do NOT qualify (`aw_id=10888`,
  AW-derived attributes) — plus a
  minimal `sources.yaml` + `site.yaml` + `dataset.yaml`. Real geometry
  means `check-reaches`, the gradient tooling, and the cross-set
  integrity guards all exercise genuine data.
- **`levels validate-dataset <dir>`**: one command, grown in stages.
  S4a ships the core invariants — id-counter rules, CSV shape, geom
  format + endpoint checks, derived-count + cross-set integrity (today
  in `test_committed_reach_geom`), reach-name rules. Later phases
  extend the same command as their machinery lands: the YAML↔CSV
  generator drift check arrives **with S1**, the `dataset.yaml`
  contract check **with S6** (no dead checks against not-yet-existing
  files). The code repo's own tests run it against the fixture; the
  data repo's CI runs it against the real dataset.
- Unit/integration tests default to the fixture; `METADATA_DIR`
  becomes a deployment setting the tests no longer read.

**S4b — the CI inversion:**

- **Code CI goes fully standalone**: the kayak_data checkout — and the
  same-named-branch pairing step #122 added — are removed entirely.
  Every code-side test runs against the fixture. (The pairing step was
  transitional; it dies here by design.)
- **Data CI checks out the code repo's `main`**, installs it, and runs
  `levels validate-dataset .` (plus the import/sync round-trip smoke).
  Note: today `kayak_data/validate.py` is stdlib-only so the data CI
  can gate edit-PRs without checking out the (private) code repo — and
  its scope is small (CSV-parses + id-counter invariants only; it
  checks none of geom/cross-set/name rules). After the inversion,
  `validate-dataset` is the **only** gate covering the heavy
  invariants data-side, so provisioning the read credential for
  `kayak_python` in kayak_data's Actions is load-bearing, not a
  nicety (a read-only deploy key, mirroring today's reverse
  arrangement — and remember the Dependabot-secret-store gap that bit
  the reverse key: provision both stores). `validate.py` stays as the
  fast stdlib first line for its small subset.
- Consequence for ordering: data PRs validate against code `main`, so
  **a dataset change that needs a code update requires the code to be
  done and merged first**. Code merges never depend on data state
  again.

**Known tensions (accepted as the cost of the goal):**

1. *Coupled changes can't be co-tested pre-merge.* A code+data pair
   that only works together: the data PR is red until the code merges,
   and the code PR proves itself only against the fixture. Discipline:
   the code PR must extend the **fixture** to exercise the new shape —
   fixture-first is the substitute for paired-branch CI.
2. *Red-until-merged is ambiguous at a glance.* A data PR awaiting its
   code dependency looks broken. Mitigation: the S6 contract check
   fails with a precise message ("dataset declares contract N; this
   code provides M"), so the red is self-explaining.
3. *Real-data-only regressions slip past code CI.* The fixture cannot
   reproduce every oddity of a large dataset (scale, legacy rows).
   They surface in data CI only on the next data PR. Mitigation: a
   **scheduled data-CI run** (cron) against code `main`, plus the prod
   pipeline's soft-fail steps as the last net.
4. *Rollback asymmetry.* Reverting merged code that the dataset has
   since adopted breaks data CI retroactively; the contract version
   makes this loud, not silent.

## S5 — bootstrap path for a new region

Resolved: **`levels init-dataset <dir>`** scaffolds the skeleton
(empty CSVs with headers, `id_counters` at 1, `site.yaml.example`,
`sources.yaml` stub, `regression/`, `site/`, `dataset.yaml` with the
current contract version, the data-CI workflow file, README). Always
matches the installed code's schema; no second artifact to maintain.
(A GitHub template repo can be stamped out *from* the command's output
later if discoverability ever warrants it.)
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

- `kayak_data/dataset.yaml`: `contract_version` — versioned
  **independently of the package version** (resolved): a small integer
  the code bumps only when the CSV/JSON shape or semantics change,
  exactly like a schema migration number. Plus `generated_at` and
  dataset-level metadata.
- The code declares the contract range it supports;
  `sync-metadata`, `import_metadata`, and `validate-dataset` check it
  and refuse loudly on mismatch — the cross-repo analogue of
  `schema_migrations`. A shape change in code ships the bump plus a
  documented dataset migration step.
- This is also what makes S4b's red-data-PR states self-explaining
  (tension 2).

## Sequencing, effort, risk

| Phase | Size | Risk notes |
|---|---|---|
| S4a fixture + validate-dataset | M | test surgery; biggest payoff; do first |
| S4b CI inversion | S | removes the #122 pairing step; adds the code-repo read key to kayak_data Actions |
| S1 sources.yaml master | M | touches fetch boot/init-db/snapshot; the generator must preserve stable ids |
| S2 regression move | S | mechanical; one-time `git mv` + path changes |
| S6 dataset.yaml contract | S | small; S4b's red-PR ergonomics depend on it — may pull forward |
| S3 site.yaml + prose templating | M–L | PHP/build sweep + fragment rendering under CSP |
| S5 init-dataset + runbook | S–M | mostly docs + scaffold command |

Cross-cutting: until S4b lands, coupled phases still use the #122
paired-branch CI checkout; after S4b, the fixture-first discipline
replaces it (tension 1). Deploy ordering rules unchanged (code first,
prod sync before the nightly snapshot).

## Resolved questions (history)

All five open questions from the first pass were settled on
2026-06-05: YAML-only `enabled:` flags (S1); historical stubs move
verbatim (S2); the fixture carries real reaches (S4); prose pages are
dataset content, chrome is engine (S3); `init-dataset` over a template
repo (S5); independent contract versioning (S6). The CI inversion
(standalone code CI; data CI against code `main`) was added as the
target architecture with its tensions accepted (S4b).
