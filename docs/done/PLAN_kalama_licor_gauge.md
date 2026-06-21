# Plan: Kalama LI-COR gauge integration

Status: COMPLETE (2026-06-21) — engine merged (kayak_python #210 parser + `fetch-licor`
step, #211 flake-fix) and dataset merged (kayak_data #67 engine pin, #69 source 363 LI-COR
replacing the calc source 354 + calc 21). Only the routine prod deploy remains
(`kayak-deploy --engine-ref 22bfac3 --dataset-ref <#69 merge>` + a hand-run
`sync-metadata --allow-deletes`). Archived to docs/done.

Investigation inputs:

- `../DB/kayak.db`, pulled from live 2026-06-21 (verified facts below).
- `Kalama.gauge.md` — the LI-COR public dashboard/API discovery.
- The live `kayak_python` fetch/parser/source/dataset architecture, verified
  against the code (file:line references throughout).

This revision (2026-06-21) folds in three grounded code reviews + live API/DB
verification. Material changes from the first draft:

- **Architecture: drop the "parser custom-fetch hook"; add a standalone
  `levels fetch-licor` step** (the established pattern for non-GET feeds —
  `fetch-usgs-ogc`, `fetch-osmb`). See § Architecture decision and Review A.
- **Cutover: the entire `db_push.sh` section was stale and is removed.** The real
  path is a `kayak_data` PR → deploy → a hand-run `sync-metadata --allow-deletes`.
  See § Deploy and cutover and Review F.
- **Added a hard prerequisite**: the engine parser/step must land *and*
  `engine_test_ref` must be bumped in `kayak_data` **before** the dataset PR can
  validate. See Review G.
- Registration, timezone, unit-string, FK, and id-counter specifics corrected.

## Verified facts (so the plan rests on evidence, not assumption)

**LI-COR API (tested live 2026-06-21):**

- `GET https://www.licor.cloud/api/v1/dashboards/4c98589d-…` → HTTP 200, dashboard
  name `Kalama River Gauge`; all three channel UUIDs present in metadata.
- `POST https://www.licor.cloud/api/v2/timeseriesdata` with the documented body
  and `dashboardUUID` → **HTTP 200 with no auth token**. Returned 3 records
  (`value.records[]`), each with `datum.valid[]` `[epoch_ms, value]` pairs.
  Sample latest values: flow `303.98 cfs`, level `6.85 feet`, temp `57.35 °F`.
- **Unit strings are display-formatted**: `metricUnits` for temperature is
  `"°F"` (degree symbol), level `"feet"`, flow `"cfs"`. Do **not** match on
  unit/display strings — match channels by **UUID** (see parser design).

**Live DB (`source`/`gauge`/`calc_expression`/`reach`), all confirmed:**

- `h=3J` → gauge id `231`; `h=5I` → source id `354` (base-62 row ids).
- Source `354`: `Kalama_ItalianCreek_calc`, agency `Calculation`,
  `calc_expression_id=21`, `fetch_url_id` NULL. **Only** source linked to gauge
  `231` (`gauge_source` row `231,354`). 381 flow observations,
  `2026-06-05T19:45Z` → `2026-06-21T14:00Z`.
- calc expression `21` is referenced by **exactly one** source (354) — safe to
  remove once 354 is gone.
- Gauge `231`: name `Kalama_ItalianCreek_calc`, display `Kalama below Italian Cr
  (calc)`, lat/lon `46.044836, -122.815384`, huc `170800030305`, WA.
- **No other calc_expression references gauge 231** (`expression` scan empty) —
  safe to rename the gauge.
- Reaches on gauge `231`: `422` (aw_2139, 600–1300), `423` (aw_2141, 1000–3000),
  `424` (aw_2140, 800–3000).

**FK behavior (live schema; `models.py`):**

- `observation.source_id` → `ON DELETE RESTRICT` (`models.py:404`).
- `latest_observation.source_id` → `CASCADE` (`models.py:436`).
- `gauge_source.{source_id,gauge_id}` → `CASCADE` (`models.py:227-230`).
- `latest_gauge_observation.source_id` → `SET NULL` (`models.py:473`).
- `source.calc_expression_id` → `SET NULL` (`models.py:198`).
- The raw DB opens with `PRAGMA foreign_keys=0`; **`sync-metadata` runs with
  `foreign_keys=ON`** (`metadata_csv.py:30`) and deletes a source's observations
  *before* the source row (`metadata_csv.py:438-456`). So sync satisfies the
  RESTRICT automatically — but a hand-run raw `DELETE FROM source` on a default
  connection would silently orphan observations. **Never hand-delete; let sync do
  it.** (See Review E.)

## Goal

Replace the **calculated** Kalama gauge source (`354`) with **measured** LI-COR
public-dashboard observations, while preserving gauge id `231`, its public handle
`h=3J`, and its three reach links. The new source is a Kalama/LI-COR adapter for
this public dashboard — not a national LI-COR platform integration (the locality
lives in dataset config; the code is dashboard-agnostic).

## LI-COR data source

Public dashboard UUID: `4c98589d-ef81-4d4f-9573-bc6062d4aae0`.

| Measurement | Channel UUID | LI-COR metric | Kayak `DataType` | Units returned |
|---|---|---|---|---|
| Water flow | `da47cdb7-c1d5-42b5-922d-b8c75f0e07b6` | `com.onset.sensordata.waterflow_us` | `flow` | `cfs` |
| Water level | `ed1d69c0-88a0-4e7b-9ffe-d66ebc004468` | `com.onset.sensordata.waterlevel_us` | `gauge` | `feet` |
| Water temperature | `46d6fb02-c314-411e-b5d5-1986d107de3f` | `com.onset.sensordata.watertemperature_us` | `temperature` | `°F` |

`DataType` members confirmed (`models.py:34-40`): `gauge, flow, inflow,
temperature` — water level maps to `gauge` (there is no `stage`/`water_level`
member). Air temperature is intentionally out of scope.

Response shape: `value.records[]` (one per requested channel),
`records[].datum.valid[]` = `[timestamp_ms, value]`, `records[].datum.error[]` =
error points. Timestamps are Unix epoch **milliseconds, absolute UTC**.

## Architecture decision

**Add a dedicated `levels fetch-licor` pipeline step. Do not add a parser
custom-fetch hook, and do not add generic POST columns to `fetch_url`.**

Rationale (Review A + Review B):

- `levels fetch` is **GET-only** — the shared client (`async_fetch_many`,
  `http_client.py:431`) has no method/body/header parameter and calls
  `session.get(...)`. A POST with a JSON body is genuinely unsupported.
- The codebase already has the right pattern for "a feed that isn't the default
  GET": **standalone CLI + pipeline steps** — `fetch-usgs-ogc`
  (`cli/fetch_usgs_ogc.py`) and `fetch-osmb` (`cli/fetch_osmb.py`), each
  synchronous (`requests`), with their own retry/timeout, registered as peer
  steps in `pipeline.py`. A `fetch-licor` sibling reuses this tested mold.
- The originally-proposed "parser exposes a custom fetch method called inside
  `levels fetch`" collides with the async `gather` (a sync POST can't join it)
  and **cannot inherit** retries, body-cap, timeout, or the batch budget — those
  live *inside* `async_fetch_many`. The standalone step never pretends to inherit
  them; it implements its own (as `fetch-usgs-ogc` does).
- A generic `method`/`headers`/`body` schema on `fetch_url` is a broad
  schema/CSV-contract/validation/docs change for one local gauge (Review B).

Keep the dashboard/channel UUIDs in **dataset config** (the `fetch_url.url`),
not in code, so a sensor/dashboard change is a data edit, not a code release
(Review C). The LI-COR source therefore *does* carry a `fetch_url` row whose URL
encodes the UUIDs; the default GET `fetch` step must **skip** it (below).

## kayak_python (engine) work

### 1. Parser `src/kayak/parsers/licor.py`, registered `@register("licor")`

Pure `parse_records(text) -> list[ObservationRecord]` (the base contract;
`ObservationRecord` is a 4-field frozen dataclass: `station, data_type,
observed_at, value` — `base.py:33-51`). Responsibilities:

- Parse `value.records[]`; for each, map the channel to a `DataType` **by channel
  UUID** (carried in the source's configured URL), with display `metricName`
  used only as a secondary diagnostic. Never key off `metricUnits`/`metricName`
  strings (`°F` proves they are display-formatted).
- Emit `flow→DataType.flow`, `waterlevel→DataType.gauge`,
  `watertemperature→DataType.temperature`. Drop air temperature.
- Convert each `valid[]` timestamp `datetime.fromtimestamp(ms / 1000, tz=UTC)` —
  **tz-aware UTC**. (No epoch-ms helper exists in the codebase; convert inline.)
  `_localize` passes tz-aware datetimes through unchanged (`base.py:152-178`), so
  there is no double-conversion.
- Filter out points `> now + 1h` (the store layer rejects future timestamps —
  `observations.py:65`; existing JSON parsers pre-filter, e.g. `nwps.py:80`).
- Emit a **single, stable station name** (e.g. `Kalama_ItalianCreek_LICOR`) for
  all rows. `parse()` warns if >1 distinct station lands on a lone source
  (`base.py:134-143`).
- Skip records with empty/missing `datum.valid` and non-numeric/non-finite
  values without poisoning the other channels.
- Override `parse()` (per the `nwps.py:91-104` pattern) only to log a
  `JSON parse error for <url>` line on malformed JSON, then `return
  super().parse(text)` (the `super()` call is required so the buffer flushes).

`parse_records` must stay **pure** — no session, no DB, no logging side effects
(`base.py:104-114`). All DB I/O is the base `parse()`/`dump_to_db` path.

### 2. Register in the hardcoded loader

`ensure_all_loaded()` is an **explicit import tuple** (`registry.py:47-55`), not
auto-discovery. Add `licor` to it, or the `@register` is invisible and
`generate-sources --check` fails with "unknown parser" (`generate_sources.py:400`).

### 3. `levels fetch-licor` — `src/kayak/cli/fetch_licor.py`

Sibling of `fetch_usgs_ogc.py`. Synchronous. For each **active** `fetch_url`
whose parser is `licor`:

- Resolve the configured URL's query params (dashboardUUID + the three channel
  UUIDs + window/interval), then **POST** to the LI-COR endpoint with
  `Content-Type/Accept: application/json` and `dashboardUUID` in the body.
- **Re-add SSRF**: call `http_client._validate_url(url)` before the POST. The
  shared validator only runs inside the GET client; a separate `requests.post`
  bypasses it (Review D). (`www.licor.cloud` resolves public → passes; the
  validator is a private-IP blocklist, not an allowlist, so no host list to edit.)
- Apply its own timeout, a small retry, and a response body cap (mirroring
  `fetch_usgs_ogc._fetch_page`).
- Build the same `source_map` the default fetcher builds (`fetch.py:208-210`) —
  factor that into a shared helper reused by both `fetch` and `fetch-licor` — and
  feed the response text to `LicorParser(...).parse(text)` so storage,
  upsert, and `unknown_station_policy` behave identically.

Single-source attribution: when a `fetch_url` has exactly one source, any emitted
station attributes to that lone source (`base.py:226-227`). The LI-COR source is
single-source, so this is automatic — but set the dataset `source.name` equal to
the parser's station string anyway, so it matches `source_map` directly and stays
correct if the source is ever made multi-source.

### 4. Make the default GET `fetch` skip LI-COR rows

Add a class attribute `BaseParser.transport = "GET"`; `LicorParser.transport =
"POST"`. In `fetch.py` work-item prep, skip rows whose
`get_parser_class(name).transport != "GET"` (debug-log the skip); `fetch-licor`
selects only `transport == "POST"`. This is a one-attribute declarative seam —
**not** an async fetch hook — so the default async path is untouched (Review A).
Otherwise the default `fetch` GETs the POST endpoint and logs `Cannot GET
/api/v2/timeseriesdata` every run.

### 5. Pipeline wiring

Insert `fetch-licor` after `fetch-usgs-ogc` (`pipeline.py:181`); add it to the
`requires=("fetch","fetch-usgs-ogc")` set of downstream steps so calc/build see
its observations. Like `fetch`, consider it a soft-fail step so a LI-COR outage
doesn't fail the whole pipeline.

### 6. Configured URL (dataset `fetch_url.url`)

```text
https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=4c98589d-ef81-4d4f-9573-bc6062d4aae0&flow=da47cdb7-c1d5-42b5-922d-b8c75f0e07b6&gauge=ed1d69c0-88a0-4e7b-9ffe-d66ebc004468&temperature=46d6fb02-c314-411e-b5d5-1986d107de3f&last=2&unit=days&interval=15&intervalUnit=minutes
```

`fetch-licor` validates: host `www.licor.cloud`; path `/api/v2/timeseriesdata`;
`dashboardUUID` present; all three channel UUIDs present; `last` bounded (1–7);
`unit`/`intervalUnit` in the supported set. **Fail closed before network I/O** on
a bad config URL.

Use a rolling 2-day window so missed runs backfill by upsert (conflict key is
`source_id, observed_at, data_type` — `observations.py:88` — so re-fetched points
overwrite, not duplicate). **Aggregation cadence (Review H):** the old calc
produced ~hourly points; LI-COR raw is 1-minute. Request a coarser
`aggregationInterval` (≈15 min) with `aggregationFunction: avg` to keep
observation volume and sparkline/plot cadence in line with other gauges. 1-minute
ingestion is ~60× the row growth; `kayak-decimate` thins old data but matching
cadence up front is cheaper.

### Engine tests

Parser (`tests/test_parsers/test_licor.py`):

- Valid 3-record JSON → flow/gauge/temperature observations with correct types
  and UTC timestamps converted from epoch ms.
- Air-temperature record ignored if present.
- Empty/missing `datum.valid` → no observations for that record; other records
  unaffected.
- Unknown channel UUID logged/ignored without poisoning valid channels.
- Malformed JSON → 0 records + the `JSON parse error` log line.
- Future timestamps (> now+1h) filtered.

Fetch step (`tests/test_cli/test_fetch_licor.py`, mock the POST):

- Default `fetch` does **not** process a `transport="POST"` row; `fetch-licor`
  does (and the default GET path is unchanged for every existing parser).
- Invalid LI-COR config URL fails closed before any network call.
- `_validate_url` is invoked (SSRF preserved).
- POST body carries `dashboardUUID`, the three channel UUIDs, the relative
  window, `aggregationFunction: avg`, and the aggregation interval.

Integration smoke: seed a scratch DB with gauge `231`, the new source, and the
`licor` fetch_url; run `fetch-licor` against a mocked response; confirm flow,
gauge, and temperature land under the new source; run `update-gauge-cache` and
confirm gauge `231` latest flow comes from the LI-COR source.

## kayak_data (dataset) work

**Edit `sources.yaml` and `calc_expression.csv`; do not hand-edit
`source.csv`/`fetch_url.csv`/`gauge_source.csv`** — those three are *generated*
from `sources.yaml` by `levels generate-sources`, and `--check` byte-compares
them in CI (`generate_sources.py:1-12`; kayak_data `validate.yml`). Field names
verified: a `sources:` entry uses `id, name, agency, gauge_id, fetch_url_id`
(+ optional `calc_expression_id, timezone`); a `fetch_urls:` entry uses
`id, url, parser, hours, enabled` (+ optional `unknown_station_policy`). The
generator projects each source's `gauge_id` into one `gauge_source` row.

Changes (single PR):

1. Add a `fetch_urls:` entry: `id: 134`, the configured URL above,
   `parser: licor`, `enabled: true`.
2. Add a `sources:` entry:

   ```yaml
   - id: 363
     name: Kalama_ItalianCreek_LICOR
     agency: LI-COR        # new agency value (existing: Calculation/USGS/NWS/…)
     gauge_id: 231
     fetch_url_id: 134
     timezone: null        # epoch-ms is absolute UTC; NULL = treat naive as UTC
   ```

3. **Remove the source `354` entry from `sources.yaml`** (this drops its
   `source`/`gauge_source` rows on regenerate — the new source `363` supplies
   gauge `231`'s replacement junction).
4. **Remove calc_expression `21` from `calc_expression.csv`.** This must be in
   the **same PR** as step 3: `generate-sources --check` rejects a source whose
   `calc_expression_id` is absent from `calc_expression.csv`
   (`generate_sources.py:426`), so removing calc 21 while 354 still references it
   fails CI (Review F).
5. Update gauge `231` in `gauge.csv` (UPDATE-by-id; observations preserved):
   drop `(calc)` from `display_name` → `Kalama below Italian Cr`; optionally
   rename `name` `Kalama_ItalianCreek_calc` → `Kalama_ItalianCreek_LICOR`
   (verified no calc references gauge 231, so the rename is safe). Keep lat/lon
   unless a surveyed sensor location is chosen (see Open questions).
6. Run `levels generate-sources <kayak_data>` to regenerate the three CSVs;
   `generate-sources --check` + `validate-dataset` must pass.

**ids:** new source `363`, new fetch_url `134` (current `id_counters.csv`
`next_id`s); bump `source→364`, `fetch_url→135`. **Do not** decrement on the
delete and **do not** reuse `354`/`21` — `validate-dataset` errors if any active
or retired id ≥ its counter (`validate_dataset.py:642`). Optionally record the
purged ids in `retired_ids.yaml` (`source: [354]`, `calc_expression: [21]`) for
provenance hygiene (not strictly required — the counters already sit above them).

Check the `source.agency` display/name-map for the new `LI-COR` value (the stored
agency strings *are* the display names: `WA DOE`, `PacifiCorp`, …). Add a map
entry if a different display label is wanted.

## Deploy and cutover

The metadata path is **CSV/registry diff → `sync-metadata`**, applied by the
paired-release deployer (`deploy/kayak-deploy.sh`), which pulls `kayak_data` at a
pinned `DATASET_REF` and runs `validate-dataset` → `migrate` → `sync-metadata` →
`import-metadata`. There is no `db_push.sh` in this path (that tool is DR-only;
the first-draft `db_push.sh`/INSERT-OR-IGNORE cutover section was stale and has
been removed — Review F).

Ordering (Review G — a hard prerequisite, not optional):

1. **Engine PR** lands the `licor` parser + `fetch-licor` step + registration +
   default-fetch skip + pipeline wiring + tests. Inert until the dataset
   references `parser: licor`.
2. **Bump `engine_test_ref` in `kayak_data`** to the merged engine commit, in its
   **own prior PR** — kayak_data CI validates the dataset against the *pinned*
   engine read from the PR base, so the dataset PR (which uses `parser: licor`)
   only validates once the pin includes the new parser. (This is the
   `dataset_ci_pin_first_bootstrap` failure mode; sequence it first.)
3. **Dataset PR** (the `sources.yaml` + `calc_expression.csv` changes above).
   CI green ⇒ merge.
4. **Deploy.** The deployer **fails closed** on a delete-containing diff (neither
   deployer auto-passes `--allow-deletes`). Cutover steps, pipeline stopped:
   - Operator runs `levels sync-metadata --allow-deletes --backup` by hand and
     reviews the printed drop counts (expect **381 observations** dropped for
     source `354`). sync deletes those observations first (satisfying the
     `RESTRICT`), then the source row; the `gauge_source` (231,354) row cascades
     away; calc 21 is removed.
   - Run `levels fetch-licor` once, then `update-gauge-cache`, `calculator`,
     `build`, `orphan-check`, `check-reaches`.
   - Re-run the deploy (now a no-op for metadata) and restart the pipeline.

No manual `DELETE FROM observation/…` SQL is needed — sync handles it under
`foreign_keys=ON`. Do **not** hand-delete on a raw connection (FK off → orphans).

## Verification

Engine, before merge:

```bash
uv run pytest -q tests/test_parsers/test_licor.py \
  tests/test_cli/test_fetch_licor.py tests/test_cli/test_fetch.py \
  tests/test_cli/test_generate_sources.py
ruff check src/ tests/ && mypy src/
```

Against a scratch DB copied from `../DB/kayak.db` (after the dataset PR merges):

```bash
cp ../DB/kayak.db /tmp/kalama-licor.db
DATABASE_URL=sqlite:////tmp/kalama-licor.db DATASET_DIR=../kayak_data \
  uv run levels sync-metadata --allow-deletes        # expect: 381 obs dropped (354)
DATABASE_URL=sqlite:////tmp/kalama-licor.db uv run levels fetch-licor --show-name
DATABASE_URL=sqlite:////tmp/kalama-licor.db uv run levels pipeline --skip-fetch
DATABASE_URL=sqlite:////tmp/kalama-licor.db uv run levels orphan-check
```

Check:

- new source `363` has latest `flow` (cfs), `gauge` (feet), `temperature` (°F→
  stored value), timestamps near the LI-COR cadence;
- gauge `231` latest flow comes from source `363`;
- reaches `422`/`423`/`424` still render with flow ranges;
- no source `354` observations remain; no orphan fetch-active source.

Post-prod-cutover spot check:

```sql
SELECT data_type, observed_at, value FROM latest_observation
 WHERE source_id = 363 ORDER BY data_type;
SELECT data_type, observed_at, value, source_id FROM latest_gauge_observation
 WHERE gauge_id = 231 ORDER BY data_type;
```

## Adversarial review findings

### Review A: the parser custom-fetch hook is the wrong abstraction (NEW, decisive)

The first draft proposed parsers optionally exposing a custom fetch method called
inside `levels fetch`. Grounded in the code: there is no parser-fetch concept
(`BaseParser` is pure text→records, `base.py:53`); the fetch path is async aiohttp
(`http_client.py:470`) so a sync POST can't join the `gather`; and retries, body
cap, timeout, and the batch budget all live inside `async_fetch_many` and
**cannot** be inherited by a hook. The established pattern for non-GET feeds is a
standalone synchronous step (`fetch-usgs-ogc`, `fetch-osmb`). **Decision:** add
`levels fetch-licor`; gate it off the default GET path with a `transport` class
attribute. Smaller, tested, no async surgery.

### Review B: a generic POST source schema is overkill (kept)

Adding `method`/`headers`/`body` to `fetch_url`/`sources.yaml` changes schema, CSV
contract, validation, docs, and tests for one local gauge, and invites storing
arbitrary POST bodies as metadata. **Decision:** keep the channel/dashboard config
in `fetch_url.url`; the `fetch-licor` step builds the POST body from it.

### Review C: don't hard-code channel UUIDs in code (kept)

Sensor/dashboard edits would otherwise become code releases. **Decision:** UUIDs
live in `fetch_url.url` (dataset-owned); the parser/step matches channels by UUID
from that config.

### Review D: a separate POST client silently loses SSRF protection (NEW)

`_validate_url` runs *only* inside the shared GET client (`http_client.py:232,306`).
`fetch-usgs-ogc` does its own `requests.get` and bypasses it. A naive `fetch-licor`
would have **no** SSRF check. **Decision:** call `http_client._validate_url(url)`
in `fetch-licor` before the POST. (Risk is low for a fixed public host, but the
config URL is dataset-editable, so validate it.)

### Review E: FK enforcement is off by default; only `sync-metadata` is safe (NEW)

The live DB opens with `PRAGMA foreign_keys=0`. A hand-run `DELETE FROM source
WHERE id=354` on a default connection would **not** trip the `RESTRICT` and would
orphan 381 observations. `sync-metadata` runs `foreign_keys=ON` and deletes
observations before the source (`metadata_csv.py:30,438-456`). **Decision:**
perform the retirement only through `sync-metadata --allow-deletes`; never by
hand SQL.

### Review F: the `db_push.sh` cutover section was stale (NEW — section removed)

The first draft devoted a large section to a `db_push.sh` cutover (local-wins,
INSERT-OR-IGNORE observation merge). `db_push.sh` is not in either deployer's path
and is DR-only. The standardized path is reviewed CSV/registry → PR →
`sync-metadata`. Using `db_push.sh` here would bypass review and be the wrong
tool. **Decision:** removed; cutover is `sync-metadata --allow-deletes`. Also
note the **same-PR coupling**: remove calc 21 and source 354's reference together
or `generate-sources --check` fails.

### Review G: engine capability must precede the dataset reference (NEW)

`kayak_data` CI validates against the engine pinned at the PR base
(`engine_test_ref`). A dataset PR using `parser: licor` will fail validation until
the pin includes the parser. **Decision:** land the engine PR, bump
`engine_test_ref` in its own prior kayak_data PR, then open the dataset PR.

### Review H: ingestion cadence / storage (NEW)

LI-COR raw is 1-minute; the retired calc was ~hourly. 1-minute ingestion is ~60×
the observation-row growth and a finer sparkline cadence than peer gauges.
**Decision:** request `aggregationInterval ≈ 15 min`, `avg`, rolling 2-day
window; revisit if finer resolution is wanted.

### Review I: measured flow ≠ the old calculated flow (sharpened with data)

At the same instant (2026-06-21), LI-COR measured **~304 cfs** while the calc
gauge's cached latest was **396 cfs** — a ~23% gap, so the reaches' flow ranges
(calibrated against the calc) may mislabel runnability. **Decision:** cut over
with existing ranges first (don't mix a source swap with reach-class edits);
collect 1–2 weeks of measured-vs-user comparison; adjust `reach_class` thresholds
in a separate data review if warranted.

### Review J: single physical sensor is a new single point of failure (NEW)

The calc source was self-healing (derived from EF Lewis + Tilton, which keep
reporting). A lone LI-COR sensor/dashboard can go offline, and gauge `231` then
goes silently stale (orphan-check won't catch a present-but-stale source — it only
flags fetch-active sources lacking a `gauge_source`). **Mitigations:** log clear
errors on 403/404/empty-record responses; consider a lightweight health check
that the configured channel UUIDs are still present in dashboard metadata
(`/api/v1/dashboards/<uuid>`); rely on the existing staleness display for the
gauge; keep `fetch-licor` soft-fail so an outage doesn't red the pipeline.

### Review K: don't reuse source 354 (kept)

Reusing `354` would mix calculated and measured provenance under one id (and its
381 calc observations) unless purged perfectly. **Decision:** retire `354`, take
new id `363`, preserve gauge `231`.

## Open questions

- **Sensor location.** LI-COR JSON exposes no lat/lon; gauge `231`'s coordinates
  are the retired calc target. Confirm the physical sensor is positioned to
  represent all three reaches before trusting the level/flow for them; update
  `gauge.csv` lat/lon if a surveyed location is available.
- **Parser name.** `licor` (chosen — the code is dashboard-agnostic) vs
  `kalama.licor` (signals local scope). Either works if the `@register` argument
  equals the dataset `parser:` string exactly.
- **Gauge `name` rename.** Drop `_calc` from the internal `name` (safe — no calc
  references it), or change only `display_name`? Cosmetic; UPDATE-by-id preserves
  observations either way.
- **Agency label.** Store/display `LI-COR` as-is, or add a `source.agency`
  name-map entry?
