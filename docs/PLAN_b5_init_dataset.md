# Plan — Batch 5 / S5: `levels init-dataset`, the new-region runbook, and the acceptance run

Batch 5 is the **completion gate** of the code/data separation project (item R8 of
`PLAN_dataset_separation.md`). It is **one engine PR, no deploy**. When it lands and
the acceptance checklist is recorded, the plan reaches formal completion (criteria
1, 4, 9, 10, 12 close).

This document is the implementation plan; the §"Adversarial self-review" section at
the end records the holes found in it and how they were resolved before coding.

## Goal

A second club (Tennessee is the running example) can scaffold its own dataset and
stand up the engine without editing a tracked engine file. Concretely, from a
**wheel-installed engine outside any checkout**:

```
levels init-dataset --example /tmp/example      # publishable example, from packaged resource
levels validate-dataset /tmp/example            # passes
levels init-db && levels sync-metadata          # loads
levels build                                    # renders
# (PHP/static smoke)
levels init-dataset /tmp/newclub                # blank scaffold (status: scaffold)
```

## What exists (verified 2026-06-18)

- The dataset **contract** (`kayak.dataset.contract`) + **layout descriptor**
  (`kayak.dataset.layout`) already define every required file, CSV header set,
  id-bearing table, and sidecar. `init-dataset` writes *through* these — it does not
  reinvent the file list.
- `validate-dataset` is the acceptance oracle. The **minimum** it requires:
  `dataset.yaml` (6 required fields), the 15 contract CSVs (header-only ok),
  `id_counters.csv` (one row per id-bearing table, `next_id ≥ 1` and above any
  active/retired id), `reaches.json`/`reaches-gradient.json` (`{}` ok), and
  `retired_ids.yaml` (literal `{}`). `site.yaml`/`region.yaml`/`map.yaml`/`assets`
  are **optional-with-fallback**. The *only* scaffold-vs-publishable difference is
  `site/{privacy,disclaimer,contact}.md`, required for `publishable`.
- The redistribution-safe **fixture** at `tests/fixtures/dataset/` is a complete
  publishable projection (public-domain NHD geometry, fixture-authored prose; see
  `tests/fixtures/build_dataset_fixture.py`). It is the natural `--example` payload —
  but it is **not shipped in the wheel** today (it's under `tests/`).
- Packaged resources resolve identically in editable + wheel installs via
  `kayak.resources.resource_dir(...)`; hatchling ships every git-tracked file under
  `src/kayak/` with no extra config.
- `scripts/wheel-smoke.sh` is the acceptance-criterion-1 seed (builds the wheel,
  installs outside the checkout, runs `levels` from `$RUNDIR`). It currently runs
  `--help`, `fetch-map-layers`, `init-db`, `build`. It does **not** yet exercise
  `init-dataset`.

## Deliverables

### D1 — Ship the example dataset as a packaged resource
Relocate `tests/fixtures/dataset/` → **`src/kayak/data/example_dataset/`** (single
source of truth; ships in the wheel). Re-point the two test references and the
`build_dataset_fixture.py` output dir at the packaged location, read via
`resource_dir("data", "example_dataset")`. No second copy → no drift.

### D2 — `levels init-dataset` command (`src/kayak/cli/init_dataset.py`)
```
levels init-dataset DIR
    [--name NAME] [--id DATASET_ID] [--license SPDX]   # scaffold identity
    [--example]                                        # copy packaged example instead
    [--ci [--engine-repo OWNER/REPO]                   # also emit a CI workflow
          [--engine-secret SECRET_NAME]]
```
- Refuses a **non-empty** destination (creates it if absent).
- **Scaffold mode** (default) writes, through `layout`/`contract`:
  - `dataset.yaml` — `contract_version: 1`, `dataset_id` (=`--id` or slug of DIR),
    `name` (=`--name` or DIR), `status: scaffold`, `license` (=`--license` or
    `CC-BY-NC-4.0`), `engine_test_ref` = 40-zero placeholder + a `# TODO pin…` note.
  - 15 header-only contract CSVs (`layout.ordered_columns`), `id_counters.csv`
    (one row per `layout.id_bearing_tables()`, `next_id=1`), `reaches.json` `{}`,
    `reaches-gradient.json` `{}`, `retired_ids.yaml` `{}`.
  - `sources.yaml` registry stub (empty `fetch_urls`/`sources`, commented), so the
    generator-owned trio round-trips.
  - `site/{privacy,disclaimer,contact}.md` TODO prose (sanitizer-clean) so a later
    `status: publishable` flip just works; `README.md`; a `PROVENANCE.json` template.
- **`--example` mode** copies `resource_dir("data","example_dataset")` into DIR
  verbatim (publishable, validates as-is).
- **Always self-validates**: runs `validate_dataset` on its own output and fails
  (non-zero, removing what it created) if it ever emits an invalid dataset.

### D3 — Optional CI workflow emission (`--ci`)
Emit `.github/workflows/validate.yml` modeled on `kayak_data/.github/workflows/validate.yml`,
parameterized: `--engine-repo` (default placeholder + comment), engine-deploy-key
secret name (`--engine-secret`, default `KAYAK_ENGINE_DEPLOY_KEY`), and the
`SITE_URL`. The trusted-base-pin logic and the validate→generate-sources→sync→build
sequence are preserved verbatim (engine-version-coupled, not club-specific). The pin
itself self-resolves from the dataset's own `dataset.yaml`.

### D4 — New-region runbook (`docs/new-region-runbook.md`)
Covers: source discovery & parser selection, trace/HUC/DEM inputs, provenance/license
choice, site/legal content, `validate-dataset`, first sync, first offline+real fetch,
build, host config, deploy, backup restore, maintainer bootstrap. **Links** (not
duplicates) the S9 migrations doc, the S7 paired-activation/install doc, and the
S8 host-config/backup doc — the four acceptance-criterion-12 pillars each keep one
owning source. Tennessee (HUC4 0601–0604, the relevant RFCs, a possible TVA adapter)
appears as an *example*, never a constant.

### D5 — Extend `scripts/wheel-smoke.sh`
After the existing `--help`, from `$RUNDIR` (outside the checkout): `init-dataset
--example $SCRATCH` → `validate-dataset $SCRATCH` → feed `$SCRATCH` as `DATASET_DIR`
to a `build`. Add `data/example_dataset/dataset.yaml` to the resource-resolution
`checks` list. This proves `--example` resolves the example from `site-packages`,
not the source tree (acceptance criterion 1).

### D6 — Tests (`tests/test_cli_init_dataset.py`)
Scaffold validates clean (`status: scaffold`); `--example` validates clean and is
byte-identical to the packaged resource; non-empty dir refused; `--ci` emits a
workflow with the repo/secret substituted and no `mousebrains` literal unless
`--engine-repo mousebrains/...`; the self-validation guard fires on a deliberately
corrupted write. Keep the existing fixture tests green after the D1 relocation.

### D7 — Consolidate + update the plan doc
Pull `docs/PLAN_dataset_separation.md` from the (stale, branch-only)
`plan-dataset-separation` branch onto this branch — making real the eighth-pass
claim "consolidated onto main" that never landed. Update its "Remaining" section:
mark **R8 in progress → done** with this PR, and after the acceptance run move the
doc to `docs/done/` per convention.

### D8 — Acceptance checklist run (criteria 1–12)
Run the end-to-end from a freshly built wheel and **record pass/fail with evidence**
in the plan doc. Where a criterion can't be fully automated here (e.g. the
clean-VM criterion 4, the live offsite-restore criterion 10), record what was
verified and what is referenced to the S7/S8 runbooks.

## Sequencing
D1 (relocate, keep tests green) → D2 (command) → D6 (tests) → D3/D4 → D5 (wheel-smoke)
→ D7 (plan doc) → D8 (acceptance run, recorded). Each step leaves the suite green.

## Adversarial self-review (verified against the code, not assumed)

**R-a — "offline fixture fetch" (criterion 1): no clean path exists; scope it, don't
hack it.** `kayak.utils.http_client._validate_url` rejects every non-`http(s)` scheme
*and* any host resolving to loopback/private/link-local/metadata IPs (lines 84–110).
So `file://…`, `http://127.0.0.1:port`, and `http://localhost` are all rejected — a
local canned-response server or file URL cannot drive `levels fetch`. Building an
offline-fetch CLI mode would mean a **test-only production bypass, which S4a
explicitly forbids** ("do not add a test-only bypass"). **Resolution:** the offline
fetch→parse→store behavior is already covered at the pytest level (parser tests +
`dump_to_db` against canned fixture text, monkeypatching `http_client`). The
wheel-smoke (D5) exercises `--help / init-dataset / validate-dataset / init-db /
sync-metadata / build / PHP` from the installed wheel offline. Criterion 1 is
recorded **met with this nuance documented**: a *networked* fetch is intentionally
not run in the offline harness because the SSRF guard (correctly) blocks local
sources; no production code path is added to enable it. This is a scoping judgment —
flagged to the operator in D8's record rather than silently dropped.

**R-b — the relocation touches ~6 references, not 2.** Verified hits on
`tests/fixtures/dataset`: `scripts/wheel-smoke.sh:122` (functional — `DATASET_DIR`),
`tests/test_scripts/test_validate_dataset.py:3` (functional — reads it),
`tests/fixtures/build_dataset_fixture.py:2` (functional — writes it), plus three
doc/comment mentions (`deploy/SETUP.md:89`, `.github/workflows/ci.yml:252`,
`src/kayak/cli/validate_dataset.py:8`). **Resolution:** relocate to
`src/kayak/data/example_dataset/`; repoint the test + builder at
`resource_dir("data","example_dataset")`; **wheel-smoke stops referencing the
source-tree path entirely** and instead materializes the dataset via `init-dataset
--example` (which is exactly D5's purpose — the relocation *enables* the
acceptance-criterion-1 improvement). Fix the three doc/comment strings. Before
moving, re-grep to confirm nothing globs `src/kayak/data/` recursively (config_data
loads named files, not a glob — but verify) so the new subdir can't be mistaken for
engine config.

**R-c — `engine_test_ref` for a scaffold: placeholder is correct.** `validate-dataset`
checks only the 40-lowercase-hex *format* (existence-in-repo is an S7 concern), so a
scaffold writes `0`×40 with a `# TODO: pin to the engine commit you validate against`
note. Resolving the live commit from a wheel is unreliable (no git), and pinning a
real commit is a deliberate human step in the runbook (D4). Resolved: placeholder +
documented TODO; the `--example` payload keeps the fixture's existing all-zero pin.

**R-d — `--ci` workflow correctness: keep the engine-coupled logic verbatim, only
parameterize the club-specific bits.** Modeled on `kayak_data/.github/workflows/validate.yml`:
substitute `--engine-repo`, the deploy-key secret name, and `SITE_URL`; preserve the
trusted-base-pin reader and the validate→generate-sources→sync→build sequence
unchanged (the pin self-resolves from the dataset's own `dataset.yaml`). Tests assert
the emitted YAML parses and contains no `mousebrains` literal unless the caller asked
for it. `--ci` is the lowest-confidence surface, so it is **opt-in** (off by default)
and its output is documented as a starting point to review, not a turnkey gate.

**Residual risk accepted:** `--ci` template fidelity to a moving upstream workflow,
and the criterion-4 (clean-VM) / criterion-10 (live offsite restore) checks that
can only be *referenced* to the S7/S8 runbooks from here, not executed on this host.
Both are recorded as such in D8 rather than claimed as fully automated.

## Plan review verification (2026-06-18, three parallel readers vs the code)

Re-verified every load-bearing claim above against the actual modules before coding.
Result: the plan is sound; the corrections below are folded into the deliverables.

- **API to write through is confirmed and named.** A writer must use, verbatim:
  `layout.CONTRACT_CSVS` (the 15 names), `layout.ordered_columns(table) -> list[str]`,
  `layout.id_bearing_tables() -> set[str]` (PK==`["id"]`; the fixture has 9:
  state, fetch_url, calc_expression, source, gauge, reach, reach_class, rating,
  guidebook), `layout.ID_COUNTERS_CSV` (`id_counters.csv`, columns `table,next_id`),
  `layout.GEOM_JSON`/`GRADIENT_JSON` (`reaches.json`/`reaches-gradient.json`),
  `contract.DATASET_YAML`, `contract.RETIRED_IDS_YAML`, `contract.CONTRACT_VERSION`.
  `dataset.yaml` required fields are the 6 named (status ∈ {scaffold, publishable};
  `provenance` is a 7th *optional* known key); `engine_test_ref` is a **40-lowercase-
  hex format** check only.
- **CORRECTION to D2 — `sources.yaml` is NOT validated and is optional.** validate-dataset
  requires the three *generated* CSVs `source.csv`/`fetch_url.csv`/`gauge_source.csv`
  (header-only OK), which `init-dataset` writes **directly** like the other 12. The
  `sources.yaml` stub is a human-convenience/round-trip aid only (consumed by
  `levels generate-sources`), not a validation requirement — write it, but do not
  imply the trio depends on it.
- **CORRECTION to R-b — the relocation is 7 functional references, and the payload is
  a whole tree.** Beyond the six listed, `tests/fixtures/build_dataset_fixture.py:345`
  is a functional drift assertion. The fixture dir is not just CSVs: it also carries
  `regression/` (6 files), `PROVENANCE.json`, `sources.yaml`, `retired_ids.yaml`, the
  two sidecars, and `site/{privacy,disclaimer,contact}.md` — **all** move to
  `src/kayak/data/example_dataset/`. `build_dataset_fixture.py` stays under `tests/`
  (dev/provenance script, not a packaged resource) but its `OUT` repoints to
  `resource_dir("data","example_dataset")`; `test_validate_dataset.py`'s `FIXTURE`
  repoints there too (it imports the builder only for hashing helpers, not to
  regenerate). wheel-smoke's `DATASET_DIR=tests/fixtures/dataset` (line 122) is
  replaced by the D5 `init-dataset --example` flow.
- **Relocation safety CONFIRMED (R-b's open "verify nothing globs src/kayak/data").**
  `config_data.py` loads only three hardcoded filenames (builder/descriptions/
  http_concurrency.yaml) — no glob/iterdir; migration discovery is `manifest.csv`-driven,
  not filesystem-globbed. A new `src/kayak/data/example_dataset/` sits cleanly beside
  `db/` and is never mistaken for engine config. Hatchling ships all git-tracked files
  under `src/kayak/` (`packages = ["src/kayak"]`, no MANIFEST/exclude), so the subtree
  ships in the wheel with **no** `pyproject` change. `resource_dir` resolves identically
  editable+wheel.
- **CLI wiring (D2 mechanics).** New subcommands register in `src/kayak/cli/main.py`:
  import the module, call `init_dataset.addArgs(subparsers)`; the module defines
  `addArgs(subparsers)` → `add_parser("init-dataset", …)` + args +
  `set_defaults(func=_main)`. (validate_dataset is the closest sibling to mirror.)
- **D3 note.** The `kayak_data` `validate.yml` is in the *other* repo, not here — the
  `--ci` template is authored from its known shape, not copied from this tree.
