# Plan: full code/data separation

Goal: make the `kayak` repository a region-agnostic engine so another
club -- Tennessee is the running example -- can deploy it on its own VM
with its own dataset repository. One deployment is an immutable engine
version, an immutable dataset revision, and host configuration/secrets.
The deployer must not edit a tracked file in the engine repository.

The final acceptance test is automated, not just documented: install a
built engine artifact in a clean environment (including its PHP/static
runtime resources), scaffold a dataset, populate the
fixture-sized Tennessee example, initialize a blank database, validate
and sync the dataset, run one offline fixture fetch, build the site, and
exercise the public PHP smoke tests without a checkout of `kayak_data`
or any WKCC-specific file.

## Decisions

Fifth-pass decisions (2026-06-06), superseding conflicting statements
from earlier passes:

- The dataset repository is the only authority for metadata. The live
  database is a downstream projection; observations, caches, editor
  accounts, proposals, and sessions remain runtime state.
- `sources.yaml` becomes the complete human-edited source registry, not
  merely a URL list. It represents fetch-backed, USGS OGC, and calculated
  sources and carries their stable IDs. `source.csv` and `fetch_url.csv`
  are generated artifacts.
- Metadata approval is a reviewed merge in the dataset repository. The
  web editor may endorse a proposal for PR creation, but only merging the
  PR is approval.
- Schema migrations stay engine-side. Region-specific metadata mutations
  do not. The existing mixed migration history is split and frozen before
  another metadata migration is added.
- Regression reports, site prose, site identity, regional navigation,
  map-layer definitions, audit suppressions, branded static assets, and
  the data license belong to the dataset.
- Host paths, service users, domains/certificates, log paths, backup
  destinations, schedules, credentials, and retention are deployment
  configuration, not dataset content. A staging and production host may
  use the same dataset revision without sharing backup or filesystem
  policy.
- The canonical dataset root setting becomes `DATASET_DIR`.
  `METADATA_DIR` remains a deprecated compatibility alias for one release.
- Runtime web assets and install templates ship in the versioned engine
  artifact; build/deploy does not reach back into a source checkout.
- The dataset contract is versioned independently of the package version.
  A dataset also records the immutable engine release/commit used by its
  required CI gate.
- Code CI is completely standalone. Dataset CI validates with its pinned
  engine release; a separate scheduled canary validates against engine
  `main`. A PR gate never floats on `main`.
- The code-side fixture contains a few real, redistribution-safe reaches.
- `levels init-dataset <dir>` scaffolds datasets; there is no separately
  maintained template repository.

Status: fifth-pass review complete; implementation has not started.

Implementation sequence: S4a -> S6 -> S4b -> S9 -> S1 -> SA -> S2 ->
S3 -> S7 -> S8 -> S5. S4 establishes the test boundary, S6 establishes
the contract and path vocabulary, S9 prevents more data migrations from
landing in code, S1 removes fetch-time metadata writes, and SA then removes
the remaining reverse-sync/editor writers before the remaining content moves.

## Boundaries

The split has three layers. Keeping them explicit prevents `site.yaml`
from becoming an untyped dump of every setting.

| Layer | Examples | Authority |
|---|---|---|
| Engine | parsers, calculator, schema, schema migrations, build/render code, packaged PHP/static chrome, validators, generic fetch/map adapters | engine repository/release artifact |
| Dataset | metadata CSV/JSON, complete source registry, site prose/identity/assets, regional links/layers, regression reports, audit suppressions, dataset license/provenance | dataset repository |
| Runtime/deployment | observations, caches, editor/proposal state, DB path, output path, service user, hostnames, TLS, SMTP/Turnstile secrets, backup remote/retention/schedule | live DB and host config |

Provider-specific code is not automatically regional data. A reusable
WA Ecology, TVA, NWRFC, or ArcGIS Feature Service adapter can remain in
the engine; the selected endpoints, bounding boxes, fields, labels, and
enabled services belong to a dataset. WKCC-only one-off and host-audit
scripts move to dataset `ops/` or an operator archive and are not part of
the supported engine surface.

## What is already reusable

The metadata CSV/JSON snapshots already live in `kayak_data`, and
`sync-metadata` already applies rows by stable ID. Calculator evaluation
uses a strict AST allowlist. Parsers have a registry, the USGS OGC fetch
selects sources from the database, PHP reads an emitted runtime-config
snapshot, and most trace/HUC/build logic is data-driven.

These are reusable mechanisms, not proof that their surrounding inputs
or deployment wrappers are separated. In particular, the current mixed
SQL migrations, runtime source auto-creation, regional static map layers,
and host-specific systemd/nginx/status code are not clean boundaries.

## Verified gap inventory

Verified against the tree and sibling `kayak_data` checkout on
2026-06-06.

| # | Coupling | Current evidence |
|---|---|---|
| G1 | Fetch/source registry is code-repo data and incomplete as a master | `data/sources.yaml` has 114 URLs but explicitly declares only 15 stations; `source.csv` has 328 rows: 121 fetch-backed, 185 USGS OGC, and 22 calculated |
| G2 | Published analysis content is code-side | `docs/regression/*` is copied to `/static/regression/`; `calc_expression.provenance_slug` points across repositories |
| G3 | Site and regional content are embedded in code | WKCC prose/email/header strings; six-state nav and resource tables; Oregon picker fallback; hardcoded guidebook IDs; map defaults; Oregon SMB popup URLs/rendering; manifest, icons, OG image, `security.txt`, and sitemap URL |
| G4 | Tests and CI require private production data | tests read `METADATA_DIR`; code CI checks out `mousebrains/kayak_data` |
| G5 | Dataset-specific operational inputs are code-side | `data/audit_ignore.yaml`; PNW regression seasons/timezones; regional download lists; Oregon SMB layer selection/bbox; regional harvest defaults |
| G6 | Deployment assumes the WKCC host | `/home/pat`, user `pat`, WKCC/mousebrains vhosts/certs/logs, fixed systemd executable paths, hardcoded status-page backup/cert checks, remote DB helper defaults |
| G7 | Web approval writes authoritative metadata out of band | `review_logic.php` updates `reach`/`reach_class`; nightly snapshot writes the result back to git |
| G8 | Backup policy is hardcoded | `kayak-backup-offsite.sh` fixes `gdrive-crypt` and retention 26; status.py repeats the remote and backup paths |
| G9 | The engine migration stream contains regional data | at least 50 migrations mutate named WKCC sources/gauges/reaches, including Montana, Klickitat, Rogue, McKenzie, Columbia, and Oregon-specific corrections |
| G10 | Dataset root and package-resource boundaries are unclear | `METADATA_DIR` now points at a repository root; `config_data.py`, build, and deploy reach into checkout-relative `data/`, `php/`, `static/`, and `public_html/`; wheels include only `src/kayak` |
| G11 | Metadata apply is not all-or-nothing on refused deletes | `sync-metadata` currently applies inserts/updates, then exits 2 while leaving refused deletions unapplied |
| G12 | Licensing/provenance is tied to WKCC | `LICENSE-DATA` names `levels.wkcc.org`, WKCC-specific sources, and one fixed license; new datasets need their own license and asset provenance |
| G13 | Additional tools write metadata directly | `levels assign-huc`, `refresh_reach_elevations.py`, `seed_gauge_display.py`, and many `docs/one-offs/*` update metadata DB rows outside sync |

Inventory commands are illustrative; the acceptance gates below replace
fragile hand-maintained lists:

```bash
rg -n -i 'wkcc|willamette|levels\.wkcc|/home/pat|gdrive-crypt|Oregon SMB' \
  src php static data scripts systemd deploy conf public_html
rg -n 'METADATA_DIR' tests
rg -l 'INSERT INTO|UPDATE (reach|gauge|source|fetch_url|calc_expression)|DELETE FROM' \
  data/db/migrations
```

## S4 -- standalone tests and CI

This is the keystone: outside users must be able to build and test the
engine without access to WKCC data.

### S4a -- fixture dataset and validator

- Add `tests/fixtures/dataset/` with two states, three gauges (USGS,
  URL-backed, calculated), one expression, and two or three real reaches.
  Geometry must be public-domain NHD-derived; copied descriptive text
  must be authored/licensed for redistribution and recorded in the
  fixture's provenance file. Do not infer eligibility from `aw_id` alone.
- Add `levels validate-dataset <dir>`, taking an explicit directory
  argument. S4a precedes S6, so the dataset root is still `METADATA_DIR`
  here; S6 renames it to `DATASET_DIR` and reroutes the validator's
  path resolution then. Likewise, S4a hardcodes the required-file and
  CSV-header lists; S6 introduces the shared contract manifest and
  reroutes validation, generation, sync, and `init-dataset` onto it, so
  these S4a lists are deliberately throwaway — do not build the manifest
  early, and do not leave the hardcoded lists behind after S6.
- Initial checks cover required files/headers, typed CSV values, stable
  IDs/counters, all foreign keys, geometry/gradient JSON shape, endpoint
  agreement, cross-set integrity, reach-name rules, URL/parser names, and
  zero unexpected files in generated locations. Later phases extend this
  same command (generator drift with S1, contract with S6).
- Tests use the fixture by default. Tests that need a custom edge case
  construct a temporary minimal dataset; no test reads a sibling clone or
  a developer's dataset root.
- The fixture flow must run `init-db` + `sync-metadata` **without state
  seeding** even though `_seed_states()`/`sources.yaml` seeding is not
  formally retired until S1: S4a's fixture CI overrides/bypasses the
  seed so no WKCC state or source row enters the fixture DB (otherwise
  the fixture violates acceptance criterion 9). S1 then removes the seed
  path engine-wide and this override becomes the default.
- Move/package every runtime engine resource -- YAML defaults, PHP, JS/CSS,
  generic images, public templates, and install templates -- under a
  package/resource API. Load them with `importlib.resources`; no runtime
  `BASE_DIR / "php"` or source-tree lookup remains. A CI job builds a wheel,
  installs it outside the checkout, and runs CLI/build/PHP-deploy smoke tests;
  editable-install success is insufficient. This wheel-smoke command set is
  the seed of acceptance criterion 1 and **grows phase by phase** — each
  phase that adds a deployer-facing command (S1 `generate-sources`, S6/S4a
  `validate-dataset` contract checks, S5 `init-dataset`) extends the
  smoke job, and S5 brings it to the full criterion-1 set.
- Keep a license/provenance manifest for the fixture. Do not copy WKCC's
  whole `LICENSE-DATA` into the engine.

### S4b -- CI inversion without a floating PR gate

- Remove the `kayak_data` checkout and same-named-branch pairing from code
  CI. Every code job runs against fixtures.
- Dataset CI installs/checks out an immutable engine tag or commit recorded
  in `dataset.yaml` and runs `validate-dataset`, a clean-DB sync, a second
  no-op sync, build smoke, and selected PHP integration tests.
- Add a scheduled, non-required canary against engine `main`. It provides
  early compatibility warning without making ordinary data PRs change
  result when `main` moves.
- Replace `kayak_data/validate.py` with the engine validator once the pinned
  engine credential/install path works. Keeping a second partial validator
  would recreate contract drift. A tiny bootstrap script may only verify
  that the pinned validator can be installed.
- For private engine access use a read-only GitHub App/deploy credential,
  available to the workflows that need it. Third-party dataset credentials
  must never grant access to the canonical WKCC dataset. Do not use
  `pull_request_target` to execute PR-controlled dataset or engine refs with
  secrets; fork PRs use a published artifact or an explicit maintainer-run
  no-secret validation path.

Coupled changes use expand/migrate/contract discipline: merge and release
backward-compatible engine support first, update the fixture, then update
the dataset and its pinned engine ref. Removal of old support is a later
engine change after deployed datasets have advanced.

## S6 -- dataset contract and root

Land this before production commands consume the newly dataset-owned files.

- Introduce `DATASET_DIR` and a `dataset_dir` config field. For one release,
  accept `METADATA_DIR` only when `DATASET_DIR` is unset, emit a deprecation
  warning, and fail if both disagree. All dataset paths resolve below this
  root; migrations and engine resources never do.
- `dataset.yaml` contains stable, reviewed metadata only: `contract_version`,
  `dataset_id`, `name`, `status` (`scaffold` or `publishable`), license/provenance
  references, and `engine_test_ref`. Do not commit a constantly changing
  `generated_at` field.
- `engine_test_ref` is a tag or full commit in the workflow's approved engine
  repository, never an arbitrary repository URL. Validation fails if the
  workflow pin and `dataset.yaml` disagree.
- The engine declares a supported contract range. Missing `dataset.yaml`
  is legacy contract 0 and is rejected by commands requiring contract 1+.
  Error messages print dataset version, supported range, and the required
  upgrade command/document.
- Define a contract manifest in engine code: required/optional paths, CSV
  headers/types, generated files, and version migrations. `init-dataset`,
  validation, generation, and sync share this manifest instead of copying
  header lists.
- `validate-dataset`, `sync-metadata`, build, source generation, regression
  deployment, and site rendering all validate the contract before reading
  content or mutating the DB/docroot.
- Add deterministic `levels upgrade-dataset <dir> --to N` transforms where
  practical. They operate on a branch/worktree and never silently upgrade a
  production clone.
- `status: scaffold` blocks production build/deploy. Publishable requires at
  least one state, reach, gauge, active source, privacy page, disclaimer,
  contact method, and a selected data license. Empty data remains valid for
  unit tests through an explicit `--allow-scaffold` path.

## S9 -- split schema history from regional data history

The current assertion that "schema + migrations are code-side" is too
broad: most later SQL files contain WKCC metadata changes.

- Classify every existing SQL file as schema-only, data-only, or mixed.
  Preserve the historical bytes for auditability.
- Before changing discovery, require every supported existing deployment to
  have applied or explicitly stamped the complete legacy set. A deployment
  behind that barrier gets a schema-only catch-up path; it must not silently
  lose a pending mixed migration.
- Move/copy data-only history to `kayak_data/history/sql/` and stop the
  engine migration runner from discovering it. Mixed files remain in a
  frozen legacy directory for existing-install audit history; their schema
  effect is represented in the current SQLAlchemy schema and, where needed,
  a schema-only compatibility migration.
- Change migration discovery to an engine-owned schema directory or explicit
  manifest. Existing `schema_migrations` rows whose files are no longer in
  the active directory are tolerated as legacy applied versions.
- Fresh databases continue to use `Base.metadata.create_all()` and stamp the
  active schema set. Existing WKCC databases get a one-time verified stamp/
  transition; no historical regional DML is replayed on a new region.
- From this point forward, metadata changes are dataset commits only. Add a
  CI guard rejecting DML against metadata tables in engine schema migrations
  unless an explicit, reviewed schema-backfill exception is present.
- Update `docs/migrations.md` to distinguish engine schema migration,
  dataset contract migration, and ordinary dataset edits.

## SA -- reviewed dataset merge is metadata approval

Today metadata reaches the live DB through both editor approval and
dataset sync. SA removes the editor write path and the snapshot used to
reconcile it.

Execution note: S1 lands immediately before SA. That ordering removes
fetch-time source creation first while retaining the snapshot for the still-live
editor path; SA then removes the remaining metadata writers and the snapshot.

### State model

- Rename the web action from "approve" to "send for data review" (or
  equivalent). `pending -> queued -> pr_open -> merged -> deployed` is the success
  path. `rejected`, `pr_closed`, `conflict`, and `worker_error` are explicit
  states. `merged` is approved; `deployed` is published. Notify the proposer
  at both boundaries so a failed or delayed deploy is not reported as live.
- Store bridge state separately from the proposal payload: request ID,
  base dataset commit, queued/reviewer identity, branch, PR number/URL,
  attempt count, lease/heartbeat, last error, and merged commit. Transitions
  use compare-and-set predicates so two workers or two review tabs are
  idempotent.
- Capture the target row's reviewed base values. If dataset `main` changed
  those fields after review, the worker marks a conflict and requires human
  re-review instead of overwriting the newer edit.

### Privileged worker boundary

- PHP-FPM receives no repository path, git executable capability, or write
  credential. It only queues an allowlisted structured request in the DB.
- A systemd worker under the deployment operator owns a repository-scoped
  credential that may push branches but cannot bypass branch protection or
  push `main`.
- The worker uses an isolated temporary clone/worktree, never the production
  dataset checkout consumed by sync/build. An unmerged branch must be
  incapable of changing production.
- Apply changes with CSV/YAML parsers, not string replacement. Re-run source
  generation when relevant, run the full dataset validator, inspect the
  resulting diff for the allowlisted target files/fields, then commit/push.
  Use argv-based subprocess calls; proposal text never becomes shell, branch,
  path, author-email, or commit-option input.
- Per-target adapters preserve stable child-row IDs. For the current
  `reach_class` proposal shape, retain IDs for unchanged class names, allocate
  never-reused IDs for additions, and delete only explicitly removed rows.
  Add `reach_class` to `id_counters.csv` before the bridge edits that table;
  do not fall back to DB autoincrement.
- Branch/commit identity is deterministic (`proposal/<id>-<attempt>`), and
  retries discover/reuse an existing branch or PR. A crash after push but
  before DB update must not create duplicate PRs.
- A reconciliation job observes merged/closed PRs, records the terminal
  state, and notifies the proposer. Deployment remains a separate controlled
  action; merge does not grant the web process deploy rights.

### Attachments and editor scope

- Attachments remain runtime evidence in DB/asset storage and outside the
  dataset contract. The PR links to the authenticated review page. Retain
  attachments for 180 days after terminal closure by default (host-overridable)
  and include the asset store in the runtime backup set; do not put unreviewed
  photos in git or require git-LFS.
- The editor/auth routes are optional engine chrome. When disabled they
  return 404 and no editor timers/worker are enabled. Accounts, sessions,
  magic links, proposal rows, and attachments are never dataset content.
- Mail display identity comes from site config; SMTP/Turnstile/session
  secrets remain host-side.

### Retire reverse synchronization

- Remove/disable `snapshot_metadata.sh`, both metadata-snapshot units, health
  check/config keys, setup instructions, workflow prose, and failure hooks.
- Replace `export_metadata.py` with a recovery-only
  `levels recover-metadata --out <empty-dir>` command. It has no dataset-root
  default and refuses an output path inside the active dataset. Recovery
  output is reviewed/imported through a normal dataset PR; it never writes the
  authoritative checkout directly.
- Standardize fresh and existing DB loads on `sync-metadata`.
  `import_metadata.py` becomes a warning compatibility wrapper for one release
  after foreign-key and exact-projection semantics match, then is removed.
- Inventory every metadata writer. Convert supported HUC assignment,
  elevation/gradient, gauge-display, geometry, guidebook, and rating-authoring
  tools to read/write a dataset worktree or emit a reviewed patch. A direct-DB
  mode may target an explicitly marked scratch DB, but refuses the configured
  production DB. Move WKCC-only mutation scripts and historical one-offs to
  dataset history/ops; they are not supported production commands.
- Add a CI writer-boundary guard plus focused tests: outside schema migration,
  scratch/test helpers, and `sync-metadata`, engine runtime code may not issue
  DML or ORM mutations against dataset-owned tables. The guard supplements
  review and should enumerate deliberate exceptions rather than rely on one
  broad regex.
- Make `sync-metadata` all-or-nothing. If deletes are present without
  `--allow-deletes`, perform no inserts or updates. Preflight contract,
  complete file set, FK impact, uniqueness, and deletion counts before the
  transaction. A successful second run is a no-op.
- Treat source IDs as durable observation identities. Normal retirement
  sets a fetch URL inactive and removes display links as appropriate; it
  does not delete a source row or its observations. Source-row deletion is
  an exceptional, separately confirmed operation.
- The phrase "DB projection" applies to dataset-owned columns. Move
  `fetch_url.last_fetched_at` into a runtime fetch-state table during this
  transition so fetch does not mutate a dataset-owned table; avoid a growing
  undocumented allowlist of mixed-ownership columns.

## S1 -- complete source registry in the dataset

End state: `sources.yaml` is the human-edited authority for every source
row and fetch URL, while generated CSVs remain the input format consumed
by generic metadata sync.

### Registry shape and stable identity

- The registry includes explicit stable numeric IDs for existing
  `fetch_url` and `source` records. IDs are part of the dataset contract
  because observations and junction rows reference them; a URL/name is not
  a safe identity across renames.
- Represent all three current source classes:
  fetch-backed sources (121 today), detached USGS OGC sources (185), and
  calculated sources (22). A source may reference at most one fetch URL or
  calc expression; USGS OGC sources explicitly declare neither and carry
  their agency/type.
- Each fetch-backed source is explicit, including source name, agency, and
  optional timezone plus parser station key/aliases when those differ from
  the stored source name. The current `stations:` timezone map is insufficient:
  106 of 121 fetch-backed source rows are parser-auto-created and absent from
  it. Migrate/enrich the YAML from the reviewed CSVs before generation is
  enabled.
- New IDs are allocated by `levels add-source` from `id_counters.csv` and
  written into YAML in the
  same change. The generator never guesses a rename as delete+add. Changing
  an existing ID or reusing a retired ID is a validation failure.
- `enabled: false` emits a retained `fetch_url.csv` row with
  `is_active=0`; it does not erase the URL/source identity. Destructive
  removals require a tombstone or an explicit allow-delete workflow.

### Generator and runtime

- `levels generate-sources --dataset <dir>` validates parser names, URLs,
  schedules, agencies, timezones, ID uniqueness/counters, source kind, and
  references to calc expressions. It writes deterministic `source.csv` and
  `fetch_url.csv` atomically.
- Registry entries may name host-secret references required by a provider,
  but never contain secret values. The fetch adapter resolves only allowlisted
  references from host config and diagnostics never print their values.
- Dataset CI regenerates into a temporary directory and byte-compares the
  committed outputs. Manual edits to generated CSVs fail CI.
- Remove `load_sources()`/`sync_sources()` from fetch and init. After S1,
  `levels fetch` reads active DB rows only. `init-db` creates schema only;
  states and all metadata arrive through `sync-metadata`.
- Retire `_seed_states()` and `_auto_create_source()`. If a parser emits an
  undeclared station, default behavior is to reject that URL's batch and make
  the fetch step nonzero so monitoring sees it. A broad feed may opt into an
  explicit `unknown_station_policy: ignore`, with counts logged; no policy
  creates DB metadata at runtime.
- During the temporary S1-to-SA interval, the nightly snapshot remains for
  editor-authored reach metadata but must stop exporting `source` and
  `fetch_url`; those two generated files have only the YAML generator as a
  writer. Land that exclusion in the same cutover as generation/runtime-sync
  removal so there is no dual-writer window.
- Move `data/audit_ignore.yaml` to the dataset and pass it through
  `DATASET_DIR`. Move provider-safe defaults from `http_concurrency.yaml` into
  packaged engine resources and expose deployment-specific per-host overrides
  through typed host config; neither belongs to the dataset.
- Update orphan checks, database-schema docs, add-gauge docs, deploy/runbook
  steps, tests, and comments that still describe runtime YAML seeding.

## S2 -- regression content in the dataset

- Move published `*.md`, `*.svg`, and `*.json` artifacts from
  `docs/regression/` to `DATASET_DIR/regression/`. Code, algorithms, and tool
  documentation remain engine-side; the dataset carries its report index.
- Build/deploy and report-generator defaults use `DATASET_DIR`. Missing
  optional regression content logs a clear "none configured" message;
  missing declared content is an error.
- Validate that every non-empty `provenance_slug` has a matching Markdown
  report; warn on orphan reports and require referenced SVG/JSON sidecars.
- Render Markdown to HTML and sanitize with an explicit `nh3` allowlist; test
  raw HTML, event handlers, unsafe URL schemes,
  and link attributes. Do not rely on author trust or CSP alone.
- Treat SVG as active same-origin content, not a harmless image. Validate or
  sanitize it with a `defusedxml`-based strict SVG allowlist that rejects
  scripts, event attributes, `foreignObject`, external references, and unsafe
  URL schemes while allowing internal fragment references used by generated
  plots. Reject nonconforming SVG; do not serve it unchanged. Validate JSON
  sidecars for schema and size.
- Move historical artifacts verbatim first, recording their license/source.
  Content normalization is a separate reviewed change.

## S3 -- site and regional presentation content

`site.yaml` is typed dataset content. The resolution order is engine
defaults < dataset site config < allowed host overrides. Secrets are never
read from the dataset.

- Site identity: display name, organization, canonical/public URL, contact,
  locale/timezone, analytics public identifier, footer/header links, map
  default/auto extent, theme colors, and social metadata.
- Region presentation: per-state resource links, weather links, nav policy,
  guidebook labels/links, and generic map-layer definitions. The list of
  available states comes from `state.csv`; no six-state allowlist or Oregon
  fallback remains.
- Prose: `site/about.md`, `disclaimer.md`, `privacy.md`, and contact intro.
  Render during build with the same sanitizer as S2 into generated fragments
  consumed by the PHP shells. Privacy, disclaimer, and contact are required
  for `publishable`; only non-legal optional pages may use generic fallback.
- Assets: dataset overrides/defaults for icons, `og-image`, manifest text,
  `security.txt` contact/expiry, robots sitemap URL, and other branded static
  files. Build validates type/size and copies them without allowing paths
  outside the dataset.
- Map layers: replace Oregon SMB constants with a generic dataset schema for
  label, endpoint/refresh adapter, output name, bbox/filter, fields, popup
  field labels/link, symbol, and default visibility. Popup HTML/templates stay
  engine-owned and escaped; the dataset does not inject arbitrary HTML or JS.
  The reusable ArcGIS fetcher and Leaflet renderer stay engine-side. Fetched
  GeoJSON is a re-creatable runtime cache under the configured state/cache
  directory, never written to the engine or dataset checkout. Optional layer
  timers are enabled only when configured.
- Static JavaScript reads one generated, non-executable `site-config.json`
  artifact rather than having constants rewritten in place or emitting
  dataset-derived JavaScript. Apply CSP, strict JSON serialization, and
  cache-busting to it.
- Sweep Python, PHP, JS, static files, manifest/security/robots, tests, and
  generated output for WKCC/domain/region assumptions. Remaining regional
  names are allowed only in provider adapters, fixtures/provenance, archived
  history, or explicit dataset paths documented by the acceptance test.

## S7 -- portable deployment and paired-release activation

Content separation is incomplete if a new club must edit systemd, nginx,
status, or scripts in the engine checkout.

- Define the supported install layout: service account `kayak`, immutable
  release virtualenvs under `/opt/kayak/releases/<release-id>`, active symlink
  `/opt/kayak/current`, state under `/var/lib/kayak`, re-creatable caches under
  `/var/cache/kayak`, logs under `/var/log/kayak`, and config/secrets under
  `/etc/kayak`. Existing WKCC paths migrate through explicit one-release
  overrides; `/home/pat` is not an engine default.
- Because systemd does not expand environment variables in executable and
  `WorkingDirectory` fields, the installer places stable wrapper executables
  under `/usr/local/libexec/kayak/` and renders units with fixed state/config
  paths. Do not claim `/etc/kayak/env` alone makes checked-in units portable.
- Generate nginx vhosts from host config (server names, docroot, certificate
  integration, log names) and generic snippets. WKCC/mousebrains vhosts and
  cert-audit scripts move out of the engine's supported defaults.
- Parameterize status checks from the same typed host config: timezone, log
  glob, filesystem mount, backup paths/unit, offsite label, certificate host,
  docroot assets, and enabled optional services.
- A stable root-owned `kayak-deploy` wrapper at
  `/usr/local/sbin/kayak-deploy`, invoked with `--engine-ref` and
  `--dataset-ref`,
  stages both immutable revisions, validates their contract before mutation,
  pauses writers, backs up the DB, applies schema migrations, performs an
  all-or-nothing metadata sync, builds a staging docroot, then atomically
  activates code/docroot and restarts services. Record both commits in a
  release manifest/status endpoint.
- The activation orchestrator is outside the release being replaced. It
  installs the pinned wheel (including packaged resources) into a new release
  virtualenv and verifies its recorded hash/signature. It cannot depend on
  code from the half-activated target release to roll itself forward or back.
- On failure, leave the previous code/docroot active and restore the DB backup
  if it was mutated. Never `git pull` a live production checkout halfway and
  rely on "code first, data second" as the transaction boundary.
- Service installation is feature-aware: editor/bridge, regional layer fetch,
  audit, and offsite backup timers are installed/enabled only when configured.
- Add a clean-VM/container deployment smoke test using non-WKCC paths, user,
  hostname, and dataset.
- Document the supported install layout, the paired `kayak-deploy
  --engine-ref --dataset-ref` activation flow, and the rollback
  boundary (this is the "paired release activation" pillar of
  acceptance criterion 12; the S5 runbook links it rather than
  duplicating it).

## S8 -- deployment-owned backup policy

This corrects the fourth-pass decision to put backup policy in the dataset.
Backups protect runtime state on one host; they are not regional content, and
staging and production may share a dataset while requiring different remotes.

- Add typed host settings for local backup directory/retention, offsite
  enablement, rclone remote/path, offsite retention, optional docroot backup,
  and status labels. Credentials remain in rclone/secret storage.
- Keep a generic weekly schedule by default. A custom cadence is a generated
  systemd timer/drop-in from host config, because a JSON value cannot alter an
  already installed `OnCalendar` directive.
- Backup scripts and `status.py` consume the same resolved config and contain
  no `gdrive-crypt`, `/home/pat/backups`, or WKCC certificate assumptions.
- Metadata is already protected by the dataset git remote. Backups cover the
  observation/runtime DB, attachment asset store, and optionally the built
  docroot. A backup set has one manifest/checksum and a consistent DB/asset
  cutoff (pause attachment writes or use immutable content-addressed assets).
  Test restore, not only upload/prune.
- Document the host-owned runtime-configuration surface (all typed host
  settings, secret references, and the backup/restore procedure) — the
  "host-owned runtime configuration" pillar of acceptance criterion 12,
  which the S5 runbook references.

## S5 -- bootstrap and new-region runbook

Implement last so the scaffold reflects the final contract, CI, content,
and deployment surfaces.

- `levels init-dataset <dir>` refuses a non-empty destination, writes through
  the shared contract manifest, and creates `dataset.yaml` with
  `status: scaffold`, complete empty CSVs, ID counters, a complete source
  registry stub, required prose TODOs, site/assets/layers directories,
  regression directory, dataset-specific license/provenance templates, README,
  and optional GitHub CI workflow.
- The generated CI workflow pins the installed engine tag/commit; it does not
  hardcode `mousebrains/kayak` or assume a secret name without explaining how
  to configure it. `--engine-repo`/`--ci` options make this explicit.
- Add `levels init-dataset --example`, which copies the licensed code-side
  fixture into a publishable example dataset and supports the end-to-end
  acceptance test without copyrighted WKCC content.
- The runbook covers source discovery, parser selection, USGS/NWPS/RFC/state
  agencies, trace/HUC/DEM inputs, provenance/license decisions, site/legal
  content, validation, first sync, first offline/real fetch, build, host config,
  deploy, backup restore, and maintainer bootstrap. It links — rather than
  duplicates — the schema-migration doc (S9), the paired-activation/install
  doc (S7), and the host-config/backup doc (S8), so acceptance criterion
  12's four pillars each have one owning source.
- Tennessee notes are examples, not constants: HUC4 0601-0604, relevant RFCs,
  and a potential TVA adapter. Regression seasons/timezones and regional
  downloads are explicit dataset/runbook inputs.

## Authentication and secrets

| Layer | Location |
|---|---|
| Login/session/Turnstile mechanisms | engine code |
| Editors, credentials, sessions, proposals, bridge state, edit history, attachments | live DB/asset store |
| SMTP, Turnstile, session, repository-worker, rclone credentials | host secret store |
| Display identity and public contact | dataset site config, host-overridable where deployment-specific |

`init-dataset` creates no users. A host bootstraps its first maintainer
with `levels seed-maintainer`. Repository approvers authenticate at the git
host and merge the PR; proposer accounts never become dataset content.

## Security and integrity gates

- Treat a merged dataset as trusted operator input for its own deployment,
  not as hostile tenant data. Existing URL validation still protects against
  mistakes and common SSRF targets. DNS-rebinding hardening is useful defense
  in depth but is not a prerequisite justified merely by another club owning
  its own repository. Web proposals cannot edit source URLs.
- Keep redirects disabled for fetches unless each redirect target is
  revalidated. If IP pinning is implemented, test TLS SNI/Host behavior,
  IPv4/IPv6, CNAMEs, and connection reuse rather than specifying an unsafe
  URL-to-IP string rewrite.
- Dataset Markdown/SVG and rendered popup fields are sanitized or escaped
  because they execute in visitors' origin even when maintainers are trusted.
- Dataset CI does no live source fetching on untrusted PRs. Networked fetch
  smoke runs only after merge or against fixed engine fixtures.
- Generated files are atomic, deterministic, path-contained, and drift-checked.
- Contract validation rejects missing required CSVs; sync must not interpret
  a missing file as "delete every row" or silently skip part of the projection.
- Destructive sync output includes affected rows and observation counts and
  requires an explicit reviewed deployment flag. Refused deletes mutate
  nothing.
- The release manifest exposes engine commit, dataset commit, contract
  version, schema migration version, and last successful sync/build.

## Acceptance criteria

The separation is complete only when all of these pass:

1. A wheel/release artifact installed outside the checkout can run `levels --help`,
   `init-dataset`, `validate-dataset`, `init-db`, `sync-metadata`, fixture
   fetch, build, and PHP/static deployment without source-tree paths.
2. Code CI has no dataset checkout, dataset secret, sibling-path dependency,
   or test reading the operator's environment.
3. Dataset required CI uses an immutable engine ref; scheduled canary `main`
   failures do not make ordinary PR results nondeterministic.
4. A clean non-WKCC install uses a different user, home/layout, hostname,
   timezone, backup path/remote, and state set without tracked engine edits.
5. Fresh install applies no historical WKCC metadata migration.
6. Engine runtime and maintenance commands never create or mutate
   dataset-owned metadata rows/columns outside `sync-metadata` or an explicit
   schema migration; operational timestamps and caches live in runtime tables.
7. A queued proposal can create exactly one validated PR across worker crashes;
   production changes only after merge and deploy.
8. Refused metadata deletes leave the DB byte/logically unchanged; accepted
   sync followed by a second sync is a no-op.
9. Site output contains no WKCC/domain/Oregon assumptions except content
   supplied by the WKCC dataset, provider names, licensed fixture provenance,
   or archived history.
10. Backup upload/prune and restore work with a non-Google test remote and no
    dataset change.
11. `status: scaffold`, missing legal pages, absent contract, incompatible
    contract, dangling provenance, undeclared stations, generated-file drift,
    unsafe Markdown, and active-content SVG each fail with a focused error.
12. Documentation describes one standard path: engine schema migration,
    dataset validation/sync, paired release activation, and host-owned runtime
    configuration.

## Sequencing, effort, and risk

| Phase | Size | Principal risk |
|---|---|---|
| S4a fixture, validator, wheel smoke | M-L | test surgery and package-resource discovery |
| S6 contract and `DATASET_DIR` | M | path compatibility and fail-closed rollout |
| S4b CI inversion | M | private engine credential and reproducible pinning |
| S9 migration split | M-L | preserving existing DB migration history |
| S1 complete source registry | L | migration of 328 stable source identities; observation preservation |
| SA proposal-to-PR bridge | L | state machine, least privilege, idempotency, conflict handling |
| S2 regression move | S-M | licensing, links, and sanitizer coverage |
| S3 site/region content | L | broad PHP/JS/static sweep and generic map layers |
| S7 portable/paired deployment | L | systemd/nginx rendering and rollback boundary |
| S8 host-owned backup | S-M | restore testing and status parity |
| S5 scaffold/runbook/acceptance | M | integration of every prior contract |

Each phase must leave production deployable and include its compatibility
step. Before S1, runtime source sync and the full snapshot remain intact. S1
ships source-registry generation, runtime-source cutover, and source/fetch
snapshot exclusion together. The editor/snapshot path remains intact for all
other metadata until SA, which deletes the reverse-sync path in one release.

## Review conclusions

Earlier passes correctly selected dataset-owned metadata, regression
content, prose, fixtures, contract versioning, and git-reviewed approval.
This pass changes four load-bearing assumptions:

1. The source YAML must model all 328 source rows with explicit stable IDs;
   the current URL/timezone YAML cannot generate `source.csv` safely.
2. Data PRs must validate against a pinned engine release, while `main` is a
   canary; a floating required gate is not reproducible.
3. Backup/deployment policy is host configuration, not dataset content.
4. Regional SQL migrations and WKCC deployment/static-map inputs are part of
   the separation scope and require dedicated phases.

Those corrections make the stated acceptance test achievable without
creating new dual-authority, identity-loss, or half-deploy failure modes.
