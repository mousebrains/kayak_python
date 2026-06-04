# GPT-5.5 Project Review - 2026-06-03

Scope: full repo review of the Python pipeline, PHP layer, static frontend/service worker, deploy/systemd/runbooks, migrations, the fresh live DB at `../DB/kayak.db`, and the metadata clone at `../kayak_data`.

Constraints honored: I did not intentionally modify anything except this file. Live DB reads used SQLite read-only URI mode (`file:../DB/kayak.db?mode=ro`). I did not run the full test/build suites because they can create caches, temp outputs, coverage files, built docroots, or other artifacts outside `gpt-5.5.md`.

Iteration log:

1. Architecture/docs/Python/deploy/DB pass: found the freshness-monitoring false negative and stale config runbook/doc issues.
2. PHP/editor/frontend pass: found the maintainer reply race; confirmed the prior service-worker `no-store` concern is fixed.
3. Metadata/live-DB pass: verified schema/metadata shape and found no new integrity failures.
4. Focused grep and code sweep over parser/fetch/write/error surfaces: no additional actionable findings beyond the list below.

## Findings

### HIGH - Healthcheck and SLO F can report green while active sources are stale or missing

`docs/slo.md` promises a per-source freshness SLO: "Pipeline freshness per source" is measured by `scripts/health-check.sh`, and the prose says it returns non-zero when any active source is older than cadence + 2 h (`docs/slo.md:23-27`, `docs/slo.md:38-43`). That is not what the implementation does.

The script checks only the single global freshest timestamp:

- `scripts/health-check.sh:35-38` selects `MAX(observed_at)` from `latest_observation`.
- `scripts/health-check.sh:52-55` compares that one global age against the threshold.
- `scripts/health-check.sh:66-70` only counts global observations in the last 2 h.
- `systemd/kayak-healthcheck.service:18-21` then pings healthchecks.io on successful script exit, so a false success becomes an external heartbeat.

Live DB evidence from `../DB/kayak.db` at review time:

```text
global MAX(latest_observation.observed_at) = 2026-06-03 15:05:00.000000
global age = 1.448 h
```

That global value is fresh enough for the current script, but active feed-backed sources were stale:

```text
147 BCKO3  NWS    nwps           active latest=2026-06-02 17:40:00 age=22.9h
180 GPR    USACE  usace.cda      active latest=2026-06-03 13:00:00 age=3.5h
300 FALO3  NWRFC  nwrfc.textplot active latest=2026-06-03 13:00:00 age=3.5h
329 GPRO3  NWRFC  nwrfc.textplot active latest=2026-06-03 13:00:00 age=3.5h
343 JDA    USACE  usace.cda      active latest=2026-06-03 13:00:00 age=3.5h
344 BON    USACE  usace.cda      active latest=2026-06-03 13:00:00 age=3.5h
```

Four USGS sources had no `latest_observation` row at all:

```text
264 14328000
281 14316800  linked to gauge 129 N_Umpqua_Glide_merge, which has 2 reaches
292 14300000
308 14208000
```

The dashboards do not close the gap. Public `/status.json` intentionally uses a 48 h stale threshold and excludes sources with no observations from the agency buckets (`php/status.php:47-51`, `php/status.php:80-99`, `php/status.php:139-142`). The internal dashboard displays per-source freshness but also uses 48 h / 7 d visual buckets and is not an alerting path (`php/_internal/index.php:24-25`, `php/_internal/index.php:138-161`, `php/_internal/index.php:315-335`).

Impact: source-level feed failures can be invisible to the actual alerting path for many hours or indefinitely if some other source keeps writing observations. This is not a cosmetic doc mismatch; it defeats the freshness SLO and can send a green external heartbeat during a partial data outage.

Fix: make `health-check.sh` query active sources, not global latest. At minimum, join `source -> fetch_url` where `fetch_url.is_active = 1`, left join `latest_observation`, group by source, and fail on missing/latest older than the expected source window. If source-specific cadences matter, encode them explicitly rather than documenting them as if they already exist. Add a regression test with one fresh source and one stale/missing active source; the healthcheck must fail.

### MEDIUM - The config-refresh runbook documents the old privileged command that the secure wrapper was built to eliminate

`docs/operations.md` still says the sudoers grant allows `levels emit-config*` and tells the operator to refresh config with:

```bash
sudo -n /home/pat/.venv/bin/levels emit-config --out /etc/kayak/runtime-config.json
```

See `docs/operations.md:446-454`.

Current deploy code does something different and materially safer:

- `scripts/deploy.sh:194-207` renders JSON unprivileged with `levels emit-config --dry-run`, then pipes it into `sudo -n /usr/local/sbin/kayak-install-runtime-config`.
- `deploy/kayak-install-runtime-config.sh:7-17` explicitly says the old `sudo levels emit-config` path was a `pat -> root` RCE because `/home/pat/.venv/bin/levels` is pat-writable.

Impact: an operator following the runbook either gets a failure under the current sudoers policy or, worse, "fixes" sudoers back to the insecure shape. This is exactly the kind of stale operational doc that causes security regressions during incidents.

Fix: update the runbook to the wrapper pipeline, remove the `levels emit-config*` wording, and include the one-time install paths for `/usr/local/sbin/kayak-install-runtime-config` and `deploy/sudoers.d/kayak-emit-config`.

### MEDIUM - The documented local PHP quick start is broken after strict runtime config

The README quick start ends with:

```bash
php -S localhost:8000 -t public_html
```

See `README.md:43-75`. It warns about `OUTPUT_DIR` afterwards (`README.md:77-82`) but never tells a developer to emit a runtime config or set `KAYAK_CONFIG_PATH`.

The PHP code now requires that JSON:

- `php/includes/config.php:93-104` loads `/etc/kayak/runtime-config.json` or `$KAYAK_CONFIG_PATH`.
- `php/includes/config.php:111-119` returns HTTP 500 and exits when the JSON is missing/unreadable.
- `php/includes/db.php:21-26` calls `Config::str('database_path')` before the `SQLITE_PATH` fallback, so the fallback cannot save a request if config loading fatals first.

The test harnesses know this and generate a config explicitly:

- `tests/php/IntegrationTestCase.php:95-127` emits a per-test runtime config.
- `tests/php/IntegrationTestCase.php:129-137` passes `KAYAK_CONFIG_PATH`.
- `tests/js/global-setup.ts:86-97` documents that every PHP page requires readable runtime config.
- `tests/js/global-setup.ts:101-113` passes `KAYAK_CONFIG_PATH` to the PHP server.

CLAUDE.md is also stale in the same direction: it says PHP gets `SQLITE_PATH` from nginx and gives the same bare `php -S` command (`CLAUDE.md:21`, `CLAUDE.md:117-121`, `CLAUDE.md:190`, `CLAUDE.md:234`).

Impact: new local setup instructions produce 500s on PHP pages even after the DB and metadata import are correct. This is a high-friction onboarding failure, and it encourages local hacks around the strict config path.

Fix: add a dev config step, for example:

```bash
levels emit-config --out /tmp/kayak-runtime-config.json
KAYAK_CONFIG_PATH=/tmp/kayak-runtime-config.json php -S localhost:8000 -t "$OUTPUT_DIR"
```

If the intended dev behavior is env fallback without JSON, that is incompatible with the current strict `Config::load()` design and needs a code change, not just a doc tweak.

### LOW - Maintainer "reply, keep pending" is not atomic and can mutate a terminal proposal

The review POST handler pre-checks `status = pending` before dispatch (`php/includes/review_handler.php:84-93`). Most terminal actions then repeat that predicate atomically in the `UPDATE` and check `rowCount()`:

- approve: `php/includes/review_logic.php:102-117`
- reject: `php/includes/review_logic.php:212-219`
- resolve: `php/includes/review_logic.php:272-279`
- reply-and-close: `php/includes/review_logic.php:289-296`

The plain reply path does not. `php/includes/review_handler.php:114-120` calls `review_send_reply()`, and `review_send_reply()` runs:

```sql
UPDATE change_request SET reviewer_note = ? WHERE id = ?
```

with no `AND status = 'pending'` and no `rowCount()` check (`php/includes/review_logic.php:244-247`). It then sends the editor an email that says the proposal is still pending (`php/includes/review_logic.php:249-260`, `php/includes/mail.php:184-191`).

Impact: two maintainer tabs can race. One tab can approve/reject/resolve the proposal while the stale reply tab later appends a note to the now-terminal row and sends a misleading "still pending" email. This is not a broad privilege issue, but it is a real consistency bug in the audit/review workflow.

Fix: change `review_send_reply()` to return `bool`, update with `WHERE id = ? AND status = 'pending'`, send email only after `rowCount() > 0`, and have the handler return the same "Already reviewed by another maintainer" message used by the terminal actions.

### LOW - `validate-config --known-env` misses the load-bearing `METADATA_DIR` typo class, and deploy does not enable the scan

`KayakConfig` has a `metadata_dir` field read from `METADATA_DIR` (`src/kayak/config.py:121-128`). The metadata split makes that env var important: README and deploy setup tell operators to clone `kayak_data` and point `METADATA_DIR` at it (`README.md:53-59`, `CLAUDE.md:19-23`).

But the typo scanner does not include the `METADATA_` prefix:

- `_CONFIG_PREFIXES` omits `METADATA_` in `src/kayak/cli/validate_config.py:34-52`.
- unknown-env warnings only run for names matching those prefixes (`src/kayak/cli/validate_config.py:105-118`).
- deploy runs plain `levels validate-config`, not `--known-env --strict` (`scripts/deploy.sh:100-109`).

Impact: a typo like `METADTA_DIR` or `METADATA_DRI` is not warned on by the scanner. On deploy, `scripts/deploy.sh:33-37` does export a correct `METADATA_DIR` from `KAYAK_DATA`, so this is not currently a production deploy breaker. It is still weak validation around one of the more important post-split paths, especially for manual `levels sync-metadata`, `import_metadata.py`, and `export_metadata.py` usage.

Fix: add `METADATA_` to `_CONFIG_PREFIXES`, add intentional non-model names such as `KAYAK_DATA` to `_EXTRA_KNOWN` if strict mode would otherwise false-positive, then run `levels validate-config --known-env --strict` in deploy.

### LOW - Stale documentation remains around removed/redirected objects

These are not primary operational failures, but they are worth cleaning because this project leans heavily on runbooks:

- `docs/operations.md:98` says `levels-test.wkcc.org/_internal/` serves the internal dashboard. Current `conf/sites/levels-test-wkcc-org:4-7` and `conf/sites/levels-test-wkcc-org:39-43` permanently redirect the whole host to `levels.wkcc.org`.
- `docs/db_sync.md:65-69` and `docs/db_sync.md:78-83` still talk about clearing/preserving `pages`. The live DB has no `pages` table, and migration `0006_drop_pages.sql` is long applied.
- `scripts/export_metadata.py:1-7` still lists `pages` among excluded cache tables even though the table is gone.

Impact: low day-to-day, but stale runbook facts are exactly how destructive DB and deploy procedures get misapplied later.

Fix: update these references now while the correct state is fresh: no `pages` table, no `_internal` on the test hostname, and config refresh through the root-owned wrapper only.

## Verification Notes

Live DB checks against `../DB/kayak.db`:

- `PRAGMA integrity_check;` returned `ok`.
- `PRAGMA foreign_key_check;` returned no rows.
- `schema_migrations` head is `0074`, matching the newest migration file.
- No leftover `_new` tables were found.
- Core counts: `source=318`, `gauge=221`, `fetch_url=115`, `reach=421`.
- `latest_observation=718`, `latest_gauge_observation=535`.
- Active fetch-backed sources without `gauge_source`: `0`.
- Broken `gauge_source` joins: `0`.
- Two gauges have no source links: `87 NF_ROGUE_LOST_CREEK_calc`, `89 MF_ROGUE_LOST_CREEK_calc`.
- Two visible/non-map-only reaches have no class rows: `405 aw_3227`, `406 aw_10730`.
- `reach.geom` coverage: `421/421`.
- `reach.gradient_profile` coverage: `421/421`.

Metadata checks against `../kayak_data`:

- `git -C ../kayak_data status --short` was clean.
- Metadata clone HEAD was `b984311`.
- The expected CSV/JSON metadata files are present, including `reach.csv`, `source.csv`, `gauge.csv`, `gauge_source.csv`, `fetch_url.csv`, `reaches.json`, and `reaches-gradient.json`.
- Prior parity sweep during this review found the CSV table row IDs and geometry/gradient JSON IDs matched the live DB. Differences were float string formatting only, not semantic drift.

Things I explicitly did not re-report:

- The service worker now respects `Cache-Control: no-store` before using CacheStorage (`static/sw.js:37-46`), so the earlier stale-authenticated-page concern appears fixed.
- The parser/fetch path has meaningful hardening: URL scheme/host validation, no redirects, body cap, timeout/budget handling, per-host concurrency, and per-URL DB commits (`src/kayak/utils/http_client.py`, `src/kayak/cli/fetch.py`, `src/kayak/parsers/base.py`).
- The metadata sync delete refusal after committing safe upserts appears intentional and documented in `scripts/deploy.sh:138-151`; I did not treat it as a bug.
