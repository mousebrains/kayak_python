# Database Schema Reference

This document describes the SQLite database schema used by the Kayak river levels system. The schema is defined in `src/kayak/db/models.py` using SQLAlchemy 2.x ORM. Fresh databases are created via `levels init-db` (which runs `Base.metadata.create_all()` and stamps every discovered migration as applied). Existing databases evolve via `data/db/migrations/NNNN_*.sql` files applied by `levels migrate` and tracked in `schema_migrations`.

## Entity-relationship overview

The schema spans five domains:

1. **Data acquisition** — gauges, sources, fetch URLs, calc expressions.
2. **Observation data** — time-series observations, plus cached "latest" rollups at source and gauge level.
3. **River reaches** — reaches, states, classes, guidebooks.
4. **Editor / moderation** — accounts, sessions, magic links, WebAuthn credentials, change requests, audit history.
5. **Lookup / housekeeping** — HUC name catalogue, schema migration tracking, and a handful of legacy tables.

25 ORM-defined tables plus the `schema_migrations` bookkeeping table (26 live). The ER diagram in [`schema-overview.svg`](schema-overview.svg) is auto-stale; trust this document and `sqlite3 $DB .schema` when they disagree.

---

## 1. Data acquisition

### `gauge`

Physical (or virtual) gauge stations that measure river conditions. Linked to data sources via the `gauge_source` M2M table and optionally to a `rating` table for gage-height ↔ flow conversion.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `name` | VARCHAR(256) | Unique, not null |
| `bank_full` | FLOAT | Bank-full **stage height** (feet) |
| `flood_stage` | FLOAT | Flood **stage height** (feet) |
| `location` | TEXT | Free-form description of where the gauge sits |
| `latitude` | NUMERIC(9, 6) | |
| `longitude` | NUMERIC(9, 6) | |
| `elevation` | FLOAT | Gauge elevation (feet) |
| `drainage_area` | FLOAT | Upstream drainage area (square miles) |
| `station_id` | TEXT | Generic agency station identifier |
| `cbtt_id` | TEXT | NWRFC CBTT identifier |
| `geos_id` | TEXT | GEOS identifier |
| `nws_id` | TEXT | National Weather Service ID |
| `nwsli_id` | TEXT | NWS Location Identifier |
| `snotel_id` | TEXT | SNOTEL station ID |
| `usgs_id` | VARCHAR(32) | USGS site number (indexed) |
| `huc` | TEXT | HUC12 watershed code for the gauge's location |
| `allow_negative_flow` | BOOLEAN | Default 0. When 1, negative flow values are accepted (tidal gauges, outflow-only sensors) |
| `rating_id` | INTEGER | FK → `rating.id` ON DELETE SET NULL |

**Indexes:** `ix_gauge_usgs_id` on `usgs_id`.

### `source`

An individual data feed that supplies observations for one or more gauges. A source is either *fetched* (has a `fetch_url_id`) or *calculated* (has a `calc_expression_id`); in healthy data exactly one of those columns is set, though the current schema does not enforce it.

`name` is indexed but intentionally **not unique** — the same physical station may have multiple source rows:
- Multi-endpoint stations (e.g., WA DOE publishes separate URLs per data type).
- Multi-agency redundancy (e.g., `LOCO3` fed by both USGS and NWRFC).

Use `.first()` (not `.scalar_one_or_none()`) when looking up by name, or disambiguate by `agency` / `fetch_url_id`. See `src/kayak/db/sources.py::get_source_by_name`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `name` | VARCHAR(256) | Indexed, not null, **not unique** |
| `agency` | VARCHAR(64) | USGS / NOAA / NWRFC / USACE / USBR / WA DOE / NWS |
| `fetch_url_id` | INTEGER | FK → `fetch_url.id` ON DELETE SET NULL |
| `calc_expression_id` | INTEGER | FK → `calc_expression.id` ON DELETE SET NULL |

**Indexes:** `ix_source_name` on `name`.

### `gauge_source`

M2M junction linking gauges to their data sources.

| Column | Type | Notes |
|---|---|---|
| `gauge_id` | INTEGER | PK, FK → `gauge.id` ON DELETE CASCADE |
| `source_id` | INTEGER | PK, FK → `source.id` ON DELETE CASCADE |

### `fetch_url`

Remote URLs to pull observation data from. Seeded from `data/sources.yaml` by `init-db` / `fetch`. The `parser` field names a registered parser class; `hours` restricts which UTC hours of the day this URL should be fetched (e.g. `"6,12,18"`).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `url` | VARCHAR(512) | Unique, not null |
| `parser` | VARCHAR(32) | Parser name (`usgs`, `nwps`, `usbr`, `wa.gov`, etc.) |
| `hours` | VARCHAR(128) | Comma-separated UTC hour list (empty = always allowed) |
| `is_active` | BOOLEAN | Default 0. `sync_sources` flips rows to 0 when they disappear from `sources.yaml` |
| `last_fetched_at` | DATETIME | Set by `fetch` on successful download |

**Indexes:** `ix_fetch_url_is_active` on `is_active`.

### `calc_expression`

Formulas for computing synthetic observations from other gauges' latest values. References use `key::gauge_name::type` or `gauge_name::type` and are resolved via `latest_gauge_observation`. Topologically sorted by the `calculator` step; cycles raise `ValueError`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `data_type` | VARCHAR(11) | `flow`, `gauge`, `inflow`, `temperature` |
| `expression` | VARCHAR(512) | Whitelisted arithmetic + `max/min/round`; `greatest()`/`least()` accepted as SQL-style aliases |
| `time_expression` | TEXT | Space-separated list of gauge-value references this expression depends on |
| `note` | TEXT | Free-form description |

### `rating` and `rating_data`

Per-gauge gage-height ↔ flow conversion tables. Populated once, then consulted by the `calc-rating` pipeline step for gauges that report only one of the two measurements.

**Dormant as of 2026-04** — 58 `rating` rows reference URLs, but no Python code path loads them into `rating_data` (0 rows). `calc-rating` is a no-op until that ingest step is written or the tables are populated manually. See the `Rating` / `RatingData` docstrings for details.

**`rating`:**

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK |
| `url` | VARCHAR(512) | Source URL for the rating table |
| `parser` | VARCHAR(32) | Parser used to load data (currently unused) |

**`rating_data`:**

| Column | Type | Notes |
|---|---|---|
| `rating_id` | INTEGER | PK part, FK → `rating.id` ON DELETE CASCADE |
| `gauge_height_ft` | FLOAT | PK part, gage height in feet |
| `flow_cfs` | FLOAT | Not null, paired flow in cfs |

---

## 2. Observation data

### `observation`

Time-series measurements from data sources. The largest table (~4.2M rows at 2026-04). Decimation (`levels decimate`, daily) keeps recent 90 days at full resolution, 90–365 days thinned to hourly, older than 365 days thinned to 6-hourly using the observation closest to the bucket midpoint.

| Column | Type | Notes |
|---|---|---|
| `source_id` | INTEGER | PK part, FK → `source.id` ON DELETE RESTRICT |
| `observed_at` | DATETIME | PK part |
| `data_type` | VARCHAR(11) | PK part |
| `value` | FLOAT | Not null |

**Indexes:** `ix_observation_source_type_time` on `(source_id, data_type, observed_at)` (matches the PK order but with data_type in the middle, optimised for per-source-per-type range scans).

### `latest_observation`

Cached most-recent reading per (source, data_type). Maintained by `store_observation` / `update_latest`. Also stores the previous observation from ≥6 hours before the latest and the implied `delta_per_hour` so the HTML tables can display trend arrows without running a query per gauge.

| Column | Type | Notes |
|---|---|---|
| `source_id` | INTEGER | PK part, FK → `source.id` ON DELETE RESTRICT |
| `data_type` | VARCHAR(11) | PK part |
| `observed_at` | DATETIME | Not null |
| `value` | FLOAT | Not null |
| `prev_observed_at` | DATETIME | Nullable — previous reading > 6 h before latest |
| `prev_value` | FLOAT | Nullable |
| `delta_per_hour` | FLOAT | Nullable |

### `latest_gauge_observation`

Cached most-recent reading per (gauge, data_type), picking the best value across every source linked to the gauge. This is the primary table read by `build` and the PHP display layer.

| Column | Type | Notes |
|---|---|---|
| `gauge_id` | INTEGER | PK part, FK → `gauge.id` ON DELETE CASCADE |
| `data_type` | VARCHAR(11) | PK part |
| `observed_at` | DATETIME | Not null |
| `value` | FLOAT | Not null |
| `prev_observed_at` | DATETIME | Nullable |
| `prev_value` | FLOAT | Nullable |
| `delta_per_hour` | FLOAT | Nullable |
| `source_id` | INTEGER | FK → `source.id` ON DELETE SET NULL — which source contributed the winning value |

---

## 3. River reaches

### `reach`

A paddleable section of river with put-in/take-out coordinates, metadata, and an optional link to a gauge for live flow data. The `geom` field stores WKT-ish `"lon lat,lon lat,…"` for map display (generated by `levels trace`).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `updated_at` | DATETIME | Stamped by `review.php` approvals and `edit.php` direct edits |
| `gauge_id` | INTEGER | FK → `gauge.id` ON DELETE SET NULL |
| `name` | VARCHAR(64) | Unique internal slug |
| `display_name` | TEXT | Rendered on the site |
| `sort_name` | VARCHAR(256) | Controls display order (indexed) |
| `river` | TEXT | River name (groups reaches) |
| `description` | TEXT | Put-in to take-out narrative |
| `difficulties` | TEXT | Rapids and hazards |
| `nature` | TEXT | Character (pool-drop, continuous, etc.) |
| `basin` | TEXT | Watershed name (mirrors the HUC8 name once `assign-huc` has run) |
| `basin_area` | FLOAT | sq mi |
| `elevation` | FLOAT | Put-in elevation (feet) |
| `elevation_lost` | FLOAT | Total drop (feet) |
| `length` | FLOAT | River miles |
| `gradient` | FLOAT | Average feet/mile |
| `max_gradient` | FLOAT | Steepest mile's gradient |
| `features` | TEXT | Notable rapids, waterfalls, log-jams |
| `latitude`, `longitude` | NUMERIC(9,6) | Midpoint |
| `latitude_start`, `longitude_start` | NUMERIC(9,6) | Put-in |
| `latitude_end`, `longitude_end` | NUMERIC(9,6) | Take-out |
| `geom` | TEXT | `"lon lat,lon lat,…"` LineString (from NHD HR flowlines) |
| `huc` | TEXT | HUC12 code from `assign-huc` |
| `no_show` | BOOLEAN | Soft-hide from public pages (default 0) |
| `map_only` | BOOLEAN | Show on map but omit from the levels table (default 0) |
| `no_flow_range` | BOOLEAN | Reviewed and confirmed: no reliable flow range available (default 0) |
| `notes` | TEXT | Internal notes |
| `optimal_flow` | FLOAT | Ideal flow (cfs) |
| `region` | TEXT | Geographic region label |
| `remoteness` | TEXT | Access-logistics notes |
| `scenery` | TEXT | |
| `season` | TEXT | Typical runnable window |
| `watershed_type` | TEXT | |
| `aw_id` | INTEGER | American Whitewater reach ID |

**Indexes:** `ix_reach_sort_name` on `sort_name` (live DBs may still see the legacy name `ix_section_sort_name`; migration 0005 renames it).

### `state`

US states used for reach geographic tagging.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `name` | VARCHAR(64) | Unique, not null |
| `abbreviation` | VARCHAR(2) | |

### `reach_state`

M2M junction linking reaches to states. A border river may belong to multiple states.

| Column | Type | Notes |
|---|---|---|
| `reach_id` | INTEGER | PK part, FK → `reach.id` ON DELETE CASCADE |
| `state_id` | INTEGER | PK part, FK → `state.id` ON DELETE CASCADE |

**Indexes:** `ix_reach_state_state_id` on `state_id` (legacy name `ix_section_state_state_id` renamed by migration 0005).

### `reach_class`

Whitewater classification ranges for a reach, with optional flow/gage thresholds. Protected by `CHECK (low IS NULL OR high IS NULL OR low <= high)` (migration 0003).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `reach_id` | INTEGER | FK → `reach.id` ON DELETE CASCADE, not null |
| `name` | VARCHAR(32) | e.g. `"III"`, `"III+(IV)"`, `"II-IV"` |
| `low` | FLOAT | Low threshold value |
| `low_data_type` | VARCHAR(11) | `flow` or `gauge` |
| `high` | FLOAT | High threshold value |
| `high_data_type` | VARCHAR(11) | `flow` or `gauge` |

### `class_description`

Reference table mapping whitewater class names to human descriptions.

| Column | Type | Notes |
|---|---|---|
| `name` | VARCHAR(32) | PK |
| `description` | TEXT | Not null |

### `guidebook`

Published guidebooks that reference river reaches (Soggy Sneakers, Paddling Oregon, etc.).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `title` | VARCHAR(256) | Not null |
| `subtitle` | VARCHAR(256) | |
| `edition` | VARCHAR(24) | |
| `author` | TEXT | |
| `url` | TEXT | |
| `sort_order` | INTEGER | Controls display order in the reach-detail page |

### `reach_guidebook`

M2M junction between reaches and guidebooks, with extra context columns.

| Column | Type | Notes |
|---|---|---|
| `reach_id` | INTEGER | PK part, FK → `reach.id` ON DELETE CASCADE |
| `guidebook_id` | INTEGER | PK part, FK → `guidebook.id` ON DELETE CASCADE |
| `page` | TEXT | Page number |
| `run` | TEXT | Run number |
| `url` | TEXT | Direct URL for web-based guidebooks |

---

## 4. Editor / moderation (Phase 1 editor feature)

Gated on the `EDITOR_FEATURE` env var (PHP reads `auth_env('EDITOR_FEATURE')`). When off, `/login.php`, `/auth.php`, `/logout.php`, `/account.php`, `/comment.php`, `/propose.php`, `/review.php` all 404.

### `editor`

An account that can propose changes or (with status=maintainer) directly apply them.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK, autoincrement |
| `email` | VARCHAR(255) | Unique, not null |
| `display_name` | VARCHAR(128) | |
| `status` | VARCHAR(10) | One of `pending`, `minimal`, `full`, `banned`, `maintainer` (default `pending`) |
| `request_note` | TEXT | Free-form note from signup |
| `created_at` | DATETIME | Default now |
| `reviewed_at` | DATETIME | Set when a maintainer promotes/bans |
| `reviewed_by` | INTEGER | FK → `editor.id` ON DELETE SET NULL |
| `last_login_at` | DATETIME | Stamped by `set_editor_session` |

**Indexes:** `ix_editor_status` on `status`.

### `editor_session`

Cookie-backed session token; only `sha256(cookie_value)` stored. Flat 7-day expiry. Logout sets `revoked_at`; `current_editor()` rejects revoked / expired / banned-owner rows.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK |
| `editor_id` | INTEGER | FK → `editor.id` ON DELETE CASCADE |
| `token_hash` | VARCHAR(64) | Unique, not null |
| `created_at` | DATETIME | Default now |
| `expires_at` | DATETIME | Not null |
| `last_seen_at` | DATETIME | Throttled updates (60s) |
| `ip` | VARCHAR(45) | Stamped on creation |
| `user_agent` | VARCHAR(512) | Stamped on creation |
| `revoked_at` | DATETIME | Set by logout |

**Indexes:** `ix_editor_session_editor_id` on `editor_id`.

### `editor_magic_link`

Single-use magic link tokens emailed for account verification / login. 30-minute expiry. Rate-limited to 5/email/hour and 20/IP/hour by `magic_link_under_throttle`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK |
| `editor_id` | INTEGER | FK → `editor.id` ON DELETE CASCADE |
| `token_hash` | VARCHAR(64) | Unique, not null |
| `created_at` | DATETIME | Default now |
| `expires_at` | DATETIME | Not null |
| `used_at` | DATETIME | Set on first consumption |
| `ip_issued` | VARCHAR(45) | |
| `next_url` | VARCHAR(512) | Landing page after sign-in |

**Indexes:** `ix_editor_magic_link_editor_id` on `editor_id`.

### `maintainer_credential`

WebAuthn / passkey credentials for maintainers. Schema lives; registration + assertion wiring is future work.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK |
| `editor_id` | INTEGER | FK → `editor.id` ON DELETE CASCADE |
| `credential_id` | VARCHAR(255) | Unique, not null |
| `public_key` | TEXT | Not null |
| `sign_count` | INTEGER | Default 0 |
| `transports` | VARCHAR(128) | |
| `nickname` | VARCHAR(64) | |
| `created_at` | DATETIME | Default now |
| `last_used_at` | DATETIME | |
| `revoked_at` | DATETIME | |

**Indexes:** `ix_maintainer_credential_editor_id` on `editor_id`.

### `change_request`

Editor-submitted proposal queue. Polymorphic `target_type`; payload is JSON shaped by target. Only maintainer approval writes into the live tables (see `review.php::review_approve`).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK |
| `target_type` | VARCHAR(11) | One of `reach`, `gauge`, `source`, `site`, `trip_report` |
| `target_id` | INTEGER | |
| `editor_id` | INTEGER | FK → `editor.id` ON DELETE CASCADE |
| `submitted_at` | DATETIME | Default now |
| `subject` | VARCHAR(256) | |
| `payload_json` | TEXT | Not null |
| `notes_to_maint` | TEXT | |
| `status` | VARCHAR(12) | `pending`, `approved`, `rejected`, `auto_applied` |
| `reviewed_at` | DATETIME | |
| `reviewed_by` | INTEGER | FK → `editor.id` ON DELETE SET NULL |
| `reviewer_note` | TEXT | |
| `applied_json` | TEXT | Snapshot of what actually got written on approve |

**Indexes:** `ix_change_request_status`, `ix_change_request_target` on (target_type, target_id), `ix_change_request_editor_id`.

### `change_request_attachment`

Uploaded binaries (trip-report photos). Phase 1 ships the schema; no upload endpoint yet.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK |
| `change_request_id` | INTEGER | FK → `change_request.id` ON DELETE CASCADE |
| `filename` | VARCHAR(256) | Not null |
| `content_type` | VARCHAR(128) | Not null |
| `size_bytes` | INTEGER | Not null |
| `sha256` | VARCHAR(64) | Not null |
| `storage_path` | VARCHAR(512) | Not null |
| `caption` | TEXT | |
| `uploaded_at` | DATETIME | Default now |

**Constraint:** UNIQUE `(change_request_id, sha256)` as `uq_attachment_request_sha`.
**Indexes:** `ix_attachment_change_request_id`.

### `edit_history`

Audit trail of fields actually written to the live tables. Populated by both the maintainer's direct-edit path (`edit.php` — Phase 6 of the 2026-04-22 plan wires this in) and by approval of a `change_request` (`review.php`). `changed_by` is `"maintainer:<editor_id>"` or `"editor:<editor_id>"`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | PK |
| `target_type` | VARCHAR(11) | `reach`, `gauge`, `source`, `site`, `trip_report` |
| `target_id` | INTEGER | |
| `change_request_id` | INTEGER | Nullable, FK → `change_request.id` ON DELETE SET NULL |
| `field` | VARCHAR(64) | Column name, or `reach_class` for the atomic replace |
| `old_value` | TEXT | |
| `new_value` | TEXT | |
| `changed_at` | DATETIME | Default now |
| `changed_by` | VARCHAR(64) | Not null |

**Indexes:** `ix_edit_history_target` on (target_type, target_id), `ix_edit_history_changed_at`.

---

## 5. Lookup / housekeeping

### `huc_name`

Human-readable watershed names for HUC2/4/6/8/10/12 codes. Populated by `levels assign-huc` from the WBD layers shipped with NHDPlus HR. Coarser levels can be derived from a 12-digit code via `substr(huc, 1, N)`; this table provides the label for any of those prefixes.

| Column | Type | Notes |
|---|---|---|
| `code` | VARCHAR(12) | PK |
| `level` | INTEGER | 2, 4, 6, 8, 10, or 12 |
| `name` | TEXT | Not null |
| `states` | TEXT | Comma-separated state abbreviations |

**Indexes:** `ix_huc_name_level` on `level`.

### `schema_migrations`

Tracks applied SQL migrations from `data/db/migrations/*.sql`. Managed by `levels migrate` and `stamp_all_known` (called by `init-db` on fresh DBs).

| Column | Type | Notes |
|---|---|---|
| `version` | TEXT | PK (e.g. `"0002"`) |
| `applied_at` | DATETIME | Not null |

### Deprecated tables

- **`alembic_version`** — vestigial from a never-adopted Alembic bootstrap. Dropped by migration **0004** (Phase 7b of the 2026-04-22 plan).
- **`pages`** — pre-rendered page cache, 0 rows and 0 readers since the Python rewrite. Dropped by migration **0006** (Phase 7d of the 2026-04-22 plan).

---

## Data flow

```
External APIs (USGS / NOAA/NWPS / NOAA/NWRFC / USACE / USBR / WA DOE)
    |
    v
fetch (data/sources.yaml → parser dispatch)  +  fetch-usgs-ogc (OGC API)
    |
    v
observation (raw time-series per source)
    |
    +---> calc-rating (gage ↔ flow via rating_data)  [dormant]
    |
    v
latest_observation (per-source cache — store_observation/update_latest)
    |
    v
update-gauge-cache → latest_gauge_observation (per-gauge cache, best value across sources)
    |
    v
calculator (evaluates calc_expression formulas → synthetic observations)
    |
    v
build (static HTML/CSV/JSON → public_html/)       +       PHP layer (dynamic pages)
                                                                |
                                                       editor / review writes to
                                                       change_request, edit_history
```

## Evolving the schema

1. Add or change a model in `src/kayak/db/models.py`.
2. **New-DB-only changes** (new tables, new NULLable columns): `Base.metadata.create_all()` picks them up. `init-db` will produce a correct schema on any fresh install.
3. **Existing-DB changes** (ALTER / DROP / rename / CHECK constraints that SQLite can't add in place, index renames, data backfills): write a new `data/db/migrations/NNNN_<description>.sql` — numbered one higher than the last file in that directory. Each migration runs in a transaction; `schema_migrations` gets a row per applied version. `levels init-db` on a fresh DB stamps every known migration without running it; `levels migrate` on an existing DB runs only the unstamped ones.
