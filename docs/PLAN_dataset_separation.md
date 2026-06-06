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
- **Approval is a reviewed merge in the data repo, not a web write**
  (new — see SA). The data repo becomes the single source of truth for
  metadata; the live DB's metadata tables are a downstream projection
  rebuilt by `sync-metadata`. `propose.php` stays as the friendly
  proposal front door, but "approve" emits a data-repo PR/commit
  instead of an out-of-band DB write. This dissolves Thread A by
  construction (T1/T3) and retires the nightly snapshot.
- **Cloud/remote backup is defined by the data repository** (new — see
  S8). The off-site target (rclone remote, retention, schedule) is
  dataset config, not a hardcoded engine constant — each deployment
  backs up to its own destination.
- **Target CI architecture (the inversion):** the code repo's CI is
  **completely standalone** — no kayak_data checkout of any kind; the
  **data repo's CI checks out the code repo's `main`** and runs the
  dataset validations with it. Cross-repo integration testing lives
  data-side, where the data is. (The SA approval model makes this even
  cleaner: with approvals already in the data repo, the data CI is
  validating the actual approval artifact.)

Status: fourth pass (2026-06-05) — adds the SA approval-model
restructure and S8 dataset-owned backup after a red-team pass; all
questions resolved. Implementation starts with S4. Sequencing: S4a →
S4b → S6 → SA → S1 → S2 → S3 → S8 → S5 (testability, then the contract
that SA/S1 lean on, then the approval restructure, then the rest).

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
| G7 | Web-approval writes the DB out-of-band | `review.php` → `review_logic.php:156/185` `UPDATE reach` / `INSERT reach_class` on the live DB; the ~40-reference editor subsystem (`editor`/`editor_session`/`editor_magic_link`, `auth.php`, `review.php`) exists to support it. The nightly `snapshot_metadata.sh` exists only to capture these writes back. (Resolved by SA.) |
| G8 | Off-site backup target hardcoded | `systemd/kayak-backup-offsite.sh` pins `REMOTE="gdrive-crypt"` + retention 26; per-deployment destinations can't be set without editing the engine. (Resolved by S8.) |

Reproduce the inventory:

```bash
grep -rln "wkcc\|levels.wkcc.org\|Willamette" src/kayak/web/ php/ --include='*.py' --include='*.php'
grep -rln "METADATA_DIR" tests/
head -30 data/sources.yaml; head -1 <kayak_data>/fetch_url.csv
```

## SA — approval is a reviewed merge in the data repo (single source of truth)

The structural move that dissolves Thread A. Today metadata reaches the
live DB two ways: editor approvals (`review.php` → `UPDATE reach`) and
the deploy-time CSV `sync-metadata`. The nightly snapshot exists only
to reconcile the first back into the repo, and that reconciliation is
the entire Thread-A race surface. SA removes the first path: **the data
repo's reviewed history becomes the only way metadata changes**, and the
DB's metadata tables are a pure projection rebuilt by `sync-metadata`.

- **`propose.php` stays** as the low-friction front door — a paddler
  with a magic-link account still submits a proposal without a GitHub
  account (keeps the contributor bar low; this was the main cost of the
  alternative).
- **"Approve" stops writing the DB.** Instead an approved
  `change_request` becomes a **data-repo branch + PR**, but **the web
  layer must not hold the kayak_data write key** — today PHP-FPM
  (`www-data`) reads zero repo paths and has no git credential or exec
  capability (SETUP.md ACLs), and putting a repo-push key behind the
  public, magic-link-authenticated surface would let a `propose.php` /
  `auth.php` compromise push arbitrary commits to the authoritative
  metadata repo. **The bridge is a privileged-worker handoff:** the web
  layer only writes an approved-request row (it already writes
  `change_request`); a **pat-side / systemd job** (holding the write
  key under the operator account, exactly where the retired snapshot's
  key lived) picks it up and opens the PR. Approval-proper is then
  **merging that PR** — reviewed, attributable, revertible in git.
  Flag this surface explicitly in the SA design review.
- **The live DB becomes downstream-only for metadata.** No out-of-band
  metadata write remains except `_auto_create_source` (T2), which SA
  makes mandatory to retire — with approvals in git, a fetch-time DB
  write to a metadata table is the lone anomaly left.
- **The nightly snapshot retires entirely.** `snapshot_metadata.sh` and
  its diverged-branch bail go away; there is nothing to capture back
  because the repo was authoritative all along. (This also removes the
  half-deploy-gap incident class.) Retirement sweep — disable/remove
  **all** of it, not just the script: `systemd/kayak-metadata-snapshot.{service,timer}`
  (the 04:30 unit pair), the `deploy/SETUP.md` "Snapshot write key"
  provisioning section + its `ReadWritePaths` binding (the key is
  re-purposed for the SA bridge worker, not a snapshot), the
  `live-tree-workflow.md` "snapshot refuses unless on main" guard, and
  any `migrate.py` / deploy.sh snapshot references. A dangling enabled
  timer calling a deleted script is an OnFailure email storm.
- **The editor subsystem becomes optional engine chrome.** A deployment
  that prefers raw PR-based editing (a small club whose maintainers
  live in git) can run with `editor`/`auth.php`/`review.php` disabled
  entirely; WKCC keeps them as the proposal front-end. Either way the
  *approval* mechanism is identical across deployments — a data-repo
  merge — so no per-deployment web-auth system is required.
- **Attachments** (`change_request_attachment`, binary trip-report
  photos) don't fit CSV. SA must place them: keep them DB/asset-store
  side and out of the metadata contract (recommended), or commit to a
  dataset asset path (git-LFS). Decide before the bridge ships.
- **Latency** trade is acceptable: a metadata edit now rides the
  PR→merge→deploy→sync cycle instead of landing instantly. Observations
  (the time-critical data) are a separate table SA never touches.

Sequenced after S6 (the contract) and before S1 (which then builds on a
world with no snapshot to dual-write).

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
- `_auto_create_source` (parsers/base.py) **must be resolved, not
  left as-is** — see threat-model T2 (its autoincrement-id rows can't
  round-trip through the YAML generator and get deleted-by-absence on
  the next sync). Recommended: retire it; every station declared in
  `sources.yaml`, unknown stations logged-and-dropped.
- Sweep: orphan-check prose, `snapshot_metadata.sh`, CLAUDE.md,
  `docs/migrations.md`, deploy.sh step list,
  `scripts/import_metadata.py`'s docstring (it documents the
  init-db-seeds-then-reimport dance S1 retires), and
  **`export_metadata.py`'s `METADATA_TABLES`** — drop `source` +
  `fetch_url` from the snapshot's dump set (T1) so the generator is
  their only writer.

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

## S8 — cloud/remote backup defined by the data repository

`kayak-backup-offsite.sh` hardcodes the rclone remote (`gdrive-crypt`)
and retention; a second deployment can't redirect its backups without
editing engine code (G8).

- **Backup destination + policy move to dataset config** — a
  `backup:` block in `site.yaml` (or a sibling `backup.yaml`): rclone
  remote name, retention counts, schedule cadence, and which artifacts
  to ship (DB always; optionally the docroot). The *credentials*
  themselves stay host-side (rclone config / secret store) — the
  dataset names the remote, the host holds the key (least-privilege:
  the data repo never carries a secret).
- The backup scripts read the policy from the emitted config snapshot
  (same channel as `database_path`). The scripts are **bash and prod
  has no `jq`** — parse the JSON with the established jq-free pattern,
  an inline `python3 -c` read (precedent: `deploy/install-config.sh`),
  not a new `jq` dependency. The engine scripts then stay generic and
  each deployment — WKCC to Google Drive, TN to wherever — backs up to
  its own destination on its own cadence.
- The local hourly/weekly SQLite-backup units are already
  deployment-neutral (they write a host path); only the **off-site**
  step carries the hardcoded remote, so S8 is narrowly scoped to it
  plus the config plumbing.
- Note the asymmetry vs the metadata repo: the data repo's git remote
  already backs up *metadata* for free; S8 is about the **observation
  time-series** (the DB content that is NOT in the data repo) and the
  built docroot.

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

## Authentication & identity layering (not part of the data/code split)

Auth spans three layers; only one is "code", and **none of it is
dataset content** — verified: the `editor`, `editor_session`,
`editor_magic_link`, `change_request`, `change_request_attachment`,
and `edit_history` tables are **not** in `export_metadata.py`'s
`METADATA_TABLES`, so they are never snapshotted to the data repo.

| Layer | What | Where it lives |
|---|---|---|
| Login mechanism | `auth.php`, magic-link mint/verify, sessions, Turnstile glue, `seed-maintainer` CLI | **code repo** (engine, region-agnostic) |
| Accounts & state | `editor` rows, sessions, magic links, the `change_request` queue, `edit_history` | **live DB only** — runtime state, like observations and the `latest_*` caches; in neither repo |
| Auth secrets | SMTP creds, `TURNSTILE_SITE_KEY`/`SECRET`, session-signing key | **host config** (`/etc/kayak/secrets.env`, 0600 root:www-data); in neither repo by design |

The load-bearing rule: **editor accounts are per-deployment DB state,
never dataset content.** Committing editor emails (PII), session
tokens, or magic links to a `kayak_data` repo — which may be public or
templated — would be a leak; keeping them DB-only is correct and must
stay that way. "Users are data" is the trap; here they are *runtime
state*.

How SA bifurcates auth by role:

- **Approvers** no longer use the web editor/review auth at all — they
  approve by **merging a data-repo PR** (GitHub auth, entirely outside
  both repos).
- **Proposers** keep the magic-link editor accounts as the
  low-friction front door (`propose.php`), but the whole editor
  subsystem becomes **optional engine chrome**: a club whose
  contributors live in git can disable it and propose via PRs
  directly; WKCC keeps it.
- The only auth thread that crosses into the split is the **display
  identity** of auth emails (`From`/reply-to, the "<org> River Levels"
  body) — deployment branding that rides `site.yaml` with the rest of
  G3/S3. The transport secret stays host-side; the accounts stay
  DB-side.

`init-dataset` therefore scaffolds **no** auth state; a new deployment
bootstraps its first maintainer with `levels seed-maintainer` against
its own DB (a host step, not a dataset step), and sets its mail/
Turnstile secrets host-side.

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

## Threat model & red-team findings (2026-06-05)

A red-team pass (attacking the design, not correctness) surfaced eight
issues; the highest are structural and must be resolved in the named
phase, not at implementation time. Two seeded worries were checked and
found **already mitigated**: the `calc_expression` `expression` column
is evaluated by a strict AST allowlist (`calculator.py::_safe_eval` —
no imports/attribute/arbitrary calls, so a malicious dataset can't
execute code), and basic SSRF is closed (`http_client._validate_url`
blocks non-http(s) + private/loopback/metadata IPs, `allow_redirects=
False`).

**T1 (S1) — dual-authority on the generated CSVs.** `source.csv` /
`fetch_url.csv` are in `export_metadata.py`'s `METADATA_TABLES`, so the
nightly snapshot keeps dumping them from the DB even after S1 makes
them YAML-generated. **Dissolved by SA** (sequenced before S1: the snapshot retires, so no
second writer remains). S1 still drops `source`/`fetch_url` from
`export_metadata.py`'s `METADATA_TABLES` as belt-and-suspenders for the
export path that no longer runs nightly — and so the generator is
unambiguously their only writer. (Only if SA were to slip *after* S1
does the live-snapshot dual-write window reappear.)

**T2 (S1) — `_auto_create_source` rows get DELETED, not "survive".**
The plan's claim is provably false: auto-created rows take a DB
autoincrement id (`parsers/base.py`), `sources.yaml` has no id field,
so the generator can't reproduce them and the next `sync-metadata`
reads them as delete-by-absence — dropping a live source's
observations. (Live DB: `MAX(source.id)=356`, 328 rows — the id space
already diverges from the counter.) **Fix in S1, pick one and specify:**
(a) retire `_auto_create_source` — every station must be declared in
`sources.yaml`, unknown stations logged-and-dropped (fail-closed,
cleanest); (b) generation merges DB-only sources back into the YAML
first; (c) a dataset-owned overlay the generator preserves.
Recommendation: (a).

**T3 (editor edits ↔ sync, the edit.php question) — RESOLVED BY SA.**
The race existed because `review.php` wrote the live DB out-of-band and
the CSV (authoritative-by-id) could overwrite it in a sync-before-
snapshot window. Under SA approvals *are* data-repo merges, so the
metadata DB is downstream-only and there is no out-of-band write to
lose — the entire window closes by construction. (Before SA lands, the
interim discipline is: don't run `sync-metadata` between an approval
and the snapshot; SA removes the footgun rather than guarding it.)

**T4 (S2/S3) — unenforced Markdown XSS.** `markdown.markdown()` passes
raw HTML through verbatim (`<img src=x onerror=…>`, `<iframe>`,
`<form>`); S2 already renders dataset `*.md` to served HTML with no
sanitizer, and S3 extends it to prose pages. "Fragments contain no
scripts" is asserted, not enforced; the generated regression HTML
ships no CSP header at all. **Fix in S2/S3:** sanitize through an
allowlist (`nh3`/`bleach`) or escape raw HTML at render; add a
`validate-dataset` check rejecting raw HTML in `*.md`; state the CSP
header applied to generated static pages. This matters most for
third-party datasets whose review is weaker than ours.

**T5 (S6) — the contract is decorative until it fails closed.**
`dataset.yaml` doesn't exist yet and S6 is sequenced last, so through
S4→S1 there is no version gate, and the plan never says what happens
when `dataset.yaml` is **absent** (every current dataset). **Fix:**
pull S6 forward to land with/before S1; absent `dataset.yaml` ⇒
contract 0 ⇒ refuse if the code requires ≥1 (fail-closed); ship a
backfill step for pre-contract datasets.

**T6 (S5) — init-dataset yields a deployable-but-empty site.** Empty
CSVs + missing prose fragments build and serve cleanly (check-reaches
over zero reaches = no issues), so a club can go live with generic
placeholder disclaimer/privacy pages (a legal footgun) and an empty
levels table that looks like an outage — zero pipeline error. **Fix:**
`dataset.yaml status: scaffold` that build/deploy refuse to publish
until flipped; the legal placeholder pages fail-closed (refuse to
serve the engine default); a "zero reaches / zero active sources"
soft-fail wired into the existing `OnFailure` chain.

**T7 (Thread B) — the SSRF threat model assumed a trusted author.**
`http_client.py`'s accepted DNS-rebinding TOCTOU is justified by a
"sources.yaml lives in a repo owned by pat" comment — **falsified** by
the separation (a TN dataset is a third-party write path). **Fix:**
re-state the threat model in the plan; close the TOCTOU by resolving
once and pinning the IP for the request; document per-deployment key
least-privilege (the kayak_data→kayak_python read key is read-only;
prod's write key is scoped to its own dataset repo; a TN deployment's
keys must never reach the canonical `kayak_data`) — provision both the
Actions and Dependabot secret stores (the [[dependabot_kayak_data_secret]]
gap bit the reverse key already).

**T8 (S2) — dangling `provenance_slug` after the move.** Nothing
cross-checks that every `calc_expression.provenance_slug` has a
matching `regression/<slug>.md`; the CSV sync and the regression-dir
copy are independent paths. **Fix:** add the pairing as a
`validate-dataset` invariant (every slug has a doc; warn on orphan
docs).

## Sequencing, effort, risk

| Phase | Size | Risk notes |
|---|---|---|
| S4a fixture + validate-dataset | M | test surgery; biggest payoff; do first |
| S4b CI inversion | S | removes the #122 pairing step; adds the code-repo read key to kayak_data Actions |
| S6 dataset.yaml contract | S | pulled forward — SA and S1 lean on it; fail-closed on absent |
| **SA approval → data-repo merge** | **L** | the keystone restructure: propose→PR bridge, snapshot retirement, attachment placement, editor subsystem made optional; resolves T1/T3, forces T2 |
| S1 sources.yaml master | M | builds on SA (no snapshot to dual-write); generator preserves stable ids; retire `_auto_create_source` |
| S2 regression move | S | mechanical; one-time `git mv` + path changes |
| S3 site.yaml + prose templating | M–L | PHP/build sweep + fragment rendering under CSP; **sanitize** rendered markdown (T4) |
| S8 dataset-owned backup | S | repoint the off-site step at config; credentials stay host-side |
| S5 init-dataset + runbook | S–M | mostly docs + scaffold command |

Cross-cutting: until S4b lands, coupled phases still use the #122
paired-branch CI checkout; after S4b, the fixture-first discipline
replaces it (tension 1). Deploy ordering: code first, then data. (The
old "prod sync before the nightly snapshot" rule is moot once SA
retires the snapshot — pre-SA it still applies.)

## Resolved questions (history)

All five open questions from the first pass were settled on
2026-06-05: YAML-only `enabled:` flags (S1); historical stubs move
verbatim (S2); the fixture carries real reaches (S4); prose pages are
dataset content, chrome is engine (S3); `init-dataset` over a template
repo (S5); independent contract versioning (S6). The CI inversion
(standalone code CI; data CI against code `main`) was added as the
target architecture with its tensions accepted (S4b). A red-team pass
then added two structural decisions: **approval moves to data-repo
merges** (SA — dissolving the Thread-A snapshot/sync races) and
**off-site backup is dataset-defined** (S8).
