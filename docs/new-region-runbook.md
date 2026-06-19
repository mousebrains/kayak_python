# New-region runbook — standing up a second club's dataset and site

This is the end-to-end path for a **new club or region** to run the kayak engine
against **its own dataset**, without editing a tracked engine file. The engine is
a package; a *dataset* (the river-levels metadata) is separate, club-owned data —
see the architecture split in [`../CLAUDE.md`](../CLAUDE.md) and
[`PLAN_dataset_separation.md`](done/PLAN_dataset_separation.md).

Each step links the **one doc that owns** its detail; this page is the spine, not
a copy of them. **Tennessee** appears as a running *example* (HUC4 0601–0604, the
LMRFC/OHRFC forecast offices, a possible TVA dam adapter) — substitute your own
region everywhere.

Throughout, `DATASET_DIR` (or a `DATASET_DIR=…` prefix) points the engine at your
dataset directory; the engine itself is installed separately (a wheel, or an
editable checkout). The dataset is the single authority for its metadata — there
is no reverse sync from a live DB back into it.

---

## 1. Scaffold the dataset

```
levels init-dataset /path/to/tennessee --name "Tennessee Whitewater" --id tennessee
```

Writes an empty, **contract-1** dataset with `status: scaffold` that already
passes `validate-dataset`: the 15 contract CSVs (header-only), `id_counters.csv`,
the `reaches.json` / `reaches-gradient.json` sidecars (`{}`), `retired_ids.yaml`
(`{}`), a `sources.yaml` stub, clean `site/{privacy,disclaimer,contact}.md`
placeholders, and a `README.md` + `PROVENANCE.json` template. It refuses a
non-empty destination and self-validates its own output.

To start from a **complete, publishable** worked example instead (the engine's own
test dataset — two states, three gauges, three traced reaches), copy it verbatim:

```
levels init-dataset /tmp/example --example
```

The example ships `status: publishable` with an **all-zero `engine_test_ref`
placeholder** (it exists for the engine's own tests; the pin is format-checked
only). If you build on it, set a real 40-hex engine commit in `dataset.yaml` (and
replace the fixture's editorial content) before treating it as your own dataset.

Put the dataset under version control in its **own repository** (the WKCC dataset
is `kayak_data`); humans change metadata by PR to that repo, never by writing the
live DB. Contract files (`dataset.yaml`, `retired_ids.yaml`, the id-counter rules)
are owned by [`PLAN_dataset_separation.md`](done/PLAN_dataset_separation.md) §S6.

## 2. Discover sources and pick parsers

Populate `sources.yaml` (the authoritative registry), then regenerate the three
generator-owned CSVs:

```
levels generate-sources /path/to/tennessee        # writes source/fetch_url/gauge_source.csv
levels generate-sources /path/to/tennessee --check # CI-style: assert the CSVs match the registry
```

For each gauge, find a real-time feed and the **parser** that reads it. Existing
parsers register via `@register("name")` in `src/kayak/parsers/` — `nwps` (NWS
NWPS), `usgs`/USGS-OGC, `usace_cda`, `nwrfc_xml`, `wa_doe`, `calc` (synthetic
gauges), and more; the `parser:` value in `sources.yaml` must match a registered
name. For Tennessee that is mostly USGS gauges plus NWPS forecast points; a
**TVA** dam-data adapter would be a new parser (subclass `BaseParser`, implement
`parse_records`). A gauge with no live feed can still be a **calculated** gauge
(a `calc_expression` over other gauges). See the add/update/remove/split
recipes in [`PLAN_add_gauges_reaches.md`](PLAN_add_gauges_reaches.md).

New rows take stable ids from `id_counters.csv` (bump `next_id`; ids only ever
increment, never reuse — guarded by `levels validate-dataset`).

## 3. Reach geometry — trace / HUC / DEM

Reach lines, gradient profiles, and HUC codes are **tool-derived dev inputs**, not
hand-edited:

- `levels trace --putin LAT,LON --takeout LAT,LON --name "…"` traces the channel
  along NHD HR flowlines (Tennessee is HUC4 0601–0604; pre-extract those with
  `scripts/extract_trace_data.sh`). Full detail: [`tracing.md`](tracing.md).
- `reach.huc` is assigned by `levels assign-huc` (point-in-polygon over WBD HUC12).
- `reach.geom` and `reach.gradient_profile` are **excluded from `reach.csv`** —
  they live in `reaches.json` / `reaches-gradient.json` and are applied by
  `scripts/import_metadata.py --geom-only` / `--gradient-only`.

These steps need the dev geo stack (`[geo]` extra, GDAL/NHD/DEM caches) and run on
a workstation, not prod. After a re-trace, regenerate the dataset with
`levels recover-metadata --out <scratch>` and commit the refreshed `reach.csv` +
the two JSON sidecars.

## 4. Provenance and license

Record where each reach's geometry, gradients, and facts came from (and their
license) in `PROVENANCE.json`, and set `dataset.yaml`'s `license`. NHD-derived
geometry and 3DEP-derived gradients are USGS public domain; **do not** copy
editorial prose (reach names/descriptions, guidebook text) from American
Whitewater or other copyrighted sources — author your own. The engine's example
dataset's `PROVENANCE.json` and `build_dataset_fixture.py` docstring show the
expected shape and the redistribution-safety rules.

## 5. Site and legal content

Replace the scaffold placeholders in `site/` with real prose: `privacy.md`,
`disclaimer.md`, `contact.md` (required for `publishable`), plus optional
`about.md`. Regional presentation (`region.yaml` / `site.yaml` / `map.yaml`) is
optional with engine fallbacks — see [`PLAN_dataset_separation.md`](done/PLAN_dataset_separation.md)
§S3. Site prose is sanitized by `validate-dataset` (no scripts, no inline
handlers — the same CSP rules the build enforces).

When the prose is real, flip `dataset.yaml`'s `status: scaffold` → `publishable`.

## 6. Validate

```
levels validate-dataset /path/to/tennessee
```

The single gate for every dataset invariant: contract manifest, the complete-CSV
projection, id-counter monotonicity vs active **and** retired ids, geometry /
endpoint format, regression-report closure, and site-prose sanitization. The
engine's CI runs this against the packaged example; your dataset repo should run
it in its own CI (model it on the `--ci` workflow `init-dataset` can emit, and on
the engine's `.github/workflows/ci.yml`). Orphan-source triage:
[`migrations.md`](migrations.md).

## 7. First load

```
DATASET_DIR=/path/to/tennessee levels init-db                       # empty schema + stamp migrations
DATASET_DIR=/path/to/tennessee levels sync-metadata                 # load CSVs by id
DATASET_DIR=/path/to/tennessee python scripts/import_metadata.py    # apply geom/gradient sidecars
```

`init-db` creates the schema; `sync-metadata` applies the CSV diff by stable id
(INSERT/UPDATE/DELETE while preserving observations); `import_metadata.py` applies
the geometry/gradient sidecars the CSV sync excludes. Schema changes (table shape)
go through a migration instead — [`migrations.md`](migrations.md).

## 8. First fetch

```
DATASET_DIR=/path/to/tennessee levels fetch          # one pass of the live fetch stage
DATASET_DIR=/path/to/tennessee levels pipeline       # fetch → … → build (the full hourly cycle)
```

`fetch` reads the active `fetch_url` rows and dispatches to the registered
parsers. Note: the engine's SSRF guard (`kayak.utils.http_client`) rejects URLs
that resolve to loopback/private/link-local/metadata IPs, so a *networked* fetch
cannot be driven from a local canned-response server — parser correctness is
exercised offline at the unit-test level (canned feed text → `parse_records` →
`dump_to_db`) instead. A station a feed emits with no matching `source` row is
dropped and flagged per the URL's `unknown_station_policy`.

## 9. Build

```
DATASET_DIR=/path/to/tennessee OUTPUT_DIR=/path/to/docroot levels build
```

Renders the per-state HTML/CSV/text into `OUTPUT_DIR` (required; it refuses the
engine or dataset trees). Serve the static output + the PHP layer locally with
`levels emit-config` + `php -S` (see [`../CLAUDE.md`](../CLAUDE.md) §"Running the
PHP Web Layer").

## 10. Host configuration

Per-host, non-secret deployment shape (timezone, docroot, release root, service
user, log globs, certificate host, backup destination, **CORS `allowed_origins`**)
lives in `/etc/kayak/host.yaml` — typed by `kayak.host.HostConfig`, with engine
defaults when absent. PHP reads the JSON snapshot `levels emit-config` writes. The
setup, ACLs, dataset clone + read deploy key, and `DATASET_DIR` wiring are in
[`SETUP.md`](../deploy/SETUP.md).

**Set `allowed_origins`** to your own status-page and site origins — it is the CORS
allow-list `status.php` uses for `/status.json`, and its keep-current default is
the WKCC origins. Leaving it unset would allow the WKCC domains cross-origin and
deny yours, so treat it as a required per-host value (like `cert_host`):

```yaml
allowed_origins: [https://status.example.org, https://levels.example.org]
```

## 11. Deploy

Production uses the immutable **paired-release** layout (`/opt/kayak/releases/<id>`
+ an atomically-relinked `current` symlink; the docroot is a regenerable cache).
The full virgin-install and activation procedure — gate, migrate, build, atomic
cutover, rollback — is [`INSTALL-paired-release.md`](../deploy/INSTALL-paired-release.md)
(S7). On deploy, `scripts/deploy.sh` runs `validate-dataset` then `sync-metadata`
+ the sidecar apply against the pulled dataset.

## 12. Backup and restore

Operator-owned encrypted offsite backup/restore (the application DB and the
bootstrap procedure) is [`offsite-backup.md`](offsite-backup.md) (S8). The dataset
is recoverable independently: it is a git repo, and `levels recover-metadata`
reconstructs a dataset from a DB into a scratch dir for disaster recovery.

## 13. Maintainer bootstrap

`init-dataset` creates no users. Bootstrap the first editor/maintainer account on
the host:

```
levels seed-maintainer --email you@example.org
```

Editor accounts, sessions, and the change-request review queue are engine
features (the `editor`/`editor_session`/`change_request` tables). Approval
*endorses* a change; the diff still lands via a dataset-repo PR + deploy (there is
no PHP path that writes metadata) — see [`PLAN_dataset_separation.md`](done/PLAN_dataset_separation.md)
§SA.

---

### The four acceptance-criterion-12 pillars (owning sources)

| Concern | Owning doc |
|---|---|
| Schema migrations & orphan triage (S9) | [`migrations.md`](migrations.md) |
| Paired-release install & activation (S7) | [`INSTALL-paired-release.md`](../deploy/INSTALL-paired-release.md) |
| Host config, ACLs, dataset clone, deploy (S7/S8) | [`SETUP.md`](../deploy/SETUP.md) |
| Encrypted offsite backup / restore (S8) | [`offsite-backup.md`](offsite-backup.md) |

Add/update/remove/split a gauge or reach: [`PLAN_add_gauges_reaches.md`](PLAN_add_gauges_reaches.md).
