# Kayak — Pre-Handoff Improvement Plan

> **For cross-check:** this plan was drafted from a macOS dev checkout (`/Users/pat/tpw/kayak/`) plus queries against a local copy of the production DB at `/Users/pat/tpw/DB/kayak.db`. A fresh Claude session on the live Debian system (DB at `/home/pat/DB/kayak.db`) should re-run the commands in §Reproduce at the end of this file and confirm or refute each finding before any change is made.
>
> Dates are absolute. References are `file:line` against the repo state on branch `main` at the time of writing.

## Context

This plan collects concrete improvement suggestions from a deep-dive review of the whole project: Python package, PHP layer, SQL schema, shell/systemd/nginx, tests, and docs. The work is motivated by an upcoming handoff to a more junior maintainer and a 2 vCPU / 2 GB RAM Debian production VM — so every recommendation below is weighed for maintainability by a novice, safety under memory pressure, and reversibility.

Most of what exists is good: in-memory SQLite test isolation with 396 tests, comprehensive pre-commit (ruff + mypy + shellcheck + biome + gitleaks + php-lint), matrix CI on 3.13/3.14, WAL mode + `foreign_keys=ON` on both DB clients, atomic symlink-swap deploy in `build.py`, full `set -euo pipefail` shell hygiene, CSRF + prepared statements + strict session cookies in `edit.php`, and a working `hardening/` stack (fail2ban, nftables, unattended-upgrades). The gaps are concentrated in three places: **silent failure modes** (pipeline swallows exceptions; PHP/Python PRAGMA mismatch), **schema evolution story** (no migration files; a handful of missing indexes), and **junior-onboarding documentation** (no runbook, no "how do I add a gauge" doc, undocumented systemd timer fleet).

The plan is framed as **suggestions to execute selectively**, grouped by priority. Nothing here requires new dependencies or rewrites; Phase A alone will materially reduce production incident risk.

---

## Findings verified directly

| # | Finding | Location |
|---|---|---|
| 1 | Pipeline catches `Exception`, logs it, continues, returns 0 — systemd sees success on step failure | `src/kayak/cli/pipeline.py:70-77` |
| 2 | `busy_timeout=5000` in PHP vs. `30000` in Python | `php/includes/db.php:21` vs `src/kayak/db/engine.py:17` |
| 3 | `Source.name` not unique **mostly by design** — 9 duplicate rows in prod DB. Per-row audit: 1 is dead (`HCR` id=935: 0 obs, NULL URL/agency, redundant gauge link); 6 are same-station-multi-endpoint (`29C100`, `30C070` — three WA DOE URLs per station, one per data type); 2–4 are same-station-multi-agency redundancy (`CSCI`, `LOCO3`, `NMFO3`, `WNFO3`). `get_source_by_name()` at `data_db.py:47` uses `.scalar_one_or_none()` which would raise `MultipleResultsFound` on any colliding name — but no production code currently calls it. | `src/kayak/db/models.py:131`, `src/kayak/db/data_db.py:45-47` |
| 4 | `systemd/kayak-pipeline.service` has no `Restart=`, `MemoryMax=`, `CPUQuota=`, `ProtectSystem=`, `NoNewPrivileges=`, `PrivateTmp=` | `systemd/kayak-pipeline.service:1-13` |
| 5 | `init_db.py` uses only `Base.metadata.create_all()`; no migration mechanism | `src/kayak/cli/init_db.py:74-102` |
| 6 | `data/db/` contains seed CSVs only, no migrations dir | — |
| 7 | `php/edit.php` CSRF / session cookies / HTTP Basic Auth are done correctly — NOT a vulnerability | `php/edit.php:13-71` |

Additional findings from exploration agents (consistent with the code structure, not each directly re-read):
- Missing indexes on `gauge_source(source_id)`, `reach_state(reach_id)`, `latest_observation(source_id)`, `latest_gauge_observation(gauge_id)`
- `calc_rating` N+1 — `src/kayak/cli/calc_rating.py:43-159`
- Parser subclasses use `*args: Any, **kwargs: Any` forwarding — `src/kayak/parsers/{usgs,usace_outflow,usbr,wa_gov}.py`
- Low test coverage on decimate & build GeoJSON/sparkline/atomic-write paths
- `RatingData` insert doesn't sort by `gauge_height_ft` despite the model's own comment requiring it
- PHP `api.php` & `latest.php` duplicate query logic
- CSP in `conf/security-headers.conf` uses tile-subdomain wildcards

---

## Phase A — Correctness & Operability (do before handoff)

**A1. Fail the pipeline on step error.**
`src/kayak/cli/pipeline.py:70-77`. Replace `except Exception: logger.error(...)` with re-raise (or collect failures and `raise SystemExit(1)` after the loop). Add `--continue-on-error` for manual recovery. *Effort: 1 hr (incl. 2 tests — exit code + flag).*

**A2. Align SQLite `busy_timeout`.**
Change `PRAGMA busy_timeout=5000` → `30000` in `php/includes/db.php:21`. Eliminates the PHP-side 503 window when Python holds the lock >5 s during hourly runs. *Effort: 15 min.*

**A3. Harden systemd units (bounded restart + resource caps).**
Apply to `systemd/kayak-pipeline.service`, `kayak-decimate.service`, `kayak-backup.service`:
```ini
Restart=on-failure
RestartSec=5min
StartLimitIntervalSec=1h
StartLimitBurst=3
MemoryMax=1G
CPUQuota=150%
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/home/pat/DB /home/pat/public_html
```
The `RestartSec=5min` + burst=3/interval=1h combo retries three times on transient failures, then stops and fires `OnFailure=` alert — no restart storm on systemic outages. `MemoryMax=1G` leaves ~1 GB headroom for nginx + PHP-FPM. `ReadWritePaths` matches the paths in `CLAUDE.md`. **Must be sequenced after A1** — without proper exit codes, `Restart=on-failure` is a no-op. *Effort: 1 hr incl. `systemd-analyze verify`.*

**A4. Versioned SQL migrations (not Alembic).**
Create `data/db/migrations/0001_*.sql` + a small `src/kayak/cli/migrate.py` (<100 LOC) that tracks applied files in a new `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT)` table. Call it from `init_db.py` after `create_all()`. Rationale in §Sequencing. *Effort: half day incl. tests & docs.*

**A5. Defuse the `get_source_by_name` footgun — do NOT add `UNIQUE(source.name)`. Clean up one dead row.**

Verified against a local copy of the live DB — 9 rows with 7 colliding names. Per-row audit (observations / gauge links / parser / last update) splits them into three buckets:

| Bucket | Rows | Action |
|---|---|---|
| **Dead row (collapsible)** | `HCR` id=935 — 0 observations, NULL URL/agency/parser. Has one `gauge_source` link to `HillsCreek_OR_Resv` (also held by the active id=3895 USACE row) | Delete after the `gauge_source` link is removed (id=3895 already covers it). See A5a. |
| **Same station, multiple data-type endpoints** | `29C100` ×3, `30C070` ×3 — WA DOE publishes separate URLs for water temp / stage / discharge per station; each source row handles one data type | Leave. Collapsing would require a source↔fetch_url M:N bridge and parser/merge rework — out of scope for handoff. `30C070` rows are stale since 2025-07-02 (station likely offline); note but do not delete. |
| **Same station, multiple agencies (redundancy / overlap)** | `CSCI` (USBR current + USGS IDWR legacy-stale), `LOCO3` (USGS current + NWRFC stale since 2026-04-05), `NMFO3` (NWS + nwps, partial data-type overlap), `WNFO3` (USGS current + NWRFC stale since 2026-04-05) | Leave. Each gives distinct value (redundancy, partial data-type coverage, or legacy-import backfill). Archiving the NWRFC stale copies is a separate judgment call once upstream status is understood. |

**A5 proper (code fix):**
- Change `src/kayak/db/data_db.py:47` from `.scalar_one_or_none()` to `.first()` so the function degrades gracefully instead of raising `MultipleResultsFound` if the junior ever calls it with `"HCR"` etc.
- Update its docstring: "Returns the first matching Source, or None. `Source.name` is not unique — same physical station may have multiple sources (different endpoints, different agencies). Pass `fetch_url_id` / `agency` to disambiguate."
- Add a test in `tests/test_data_db.py` that creates two sources with the same name and asserts `get_source_by_name` returns one (not crashes).
- Add a one-paragraph note to `docs/database-schema.md` explaining *why* `source.name` isn't unique and what the three buckets are, so the next person doesn't "fix" it. Leave CSV/DB schema unchanged.

**A5a (optional data cleanup, same PR or separate):**
- Pre-check: `SELECT * FROM gauge_source WHERE source_id=935;` — confirm no orphaned FKs.
- `DELETE FROM gauge_source WHERE source_id=935;` (link is redundant with id=3895).
- `DELETE FROM source WHERE id=935;`.
- Same check in the seed CSV `data/db/source.csv` — remove the 935 row so fresh DBs don't re-introduce the ghost.

*Effort: 30 min (A5 proper) + 15 min (A5a if done).*

**A6. Migration 0001: missing indexes.**
`CREATE INDEX` on `gauge_source(source_id)`, `reach_state(reach_id)`, `latest_observation(source_id)`, `latest_gauge_observation(gauge_id)`. Mirror in `models.py` via `Index(...)` in `__table_args__`. *Effort: 30 min.*

**A7. Fix nginx `php` rate-limit zone on cold deploys.**
Move the `limit_req_zone ... zone=php:10m rate=2r/s;` definition from `scripts/security-harden.sh` into `deploy/nginx-ratelimit.conf`. Without this, a fresh `nginx -t` on a box where the hardening script hasn't run fails because `conf/levels.nginx:78` references `zone=php`. *Effort: 15 min.*

---

## Phase B — Maintainability & Docs (before or at handoff)

**B1. Write `docs/RUNBOOK.md`** — single page per scenario, all commands copy-pasteable:
- a. **Add a new gauge/source** — exact YAML + SQL + CLI steps (today scattered across `CLAUDE.md`, `CONTRIBUTING.md`, and undocumented `scripts/`).
- b. **Pipeline failed overnight** — `journalctl -u kayak-pipeline.service`, `scripts/health-check.sh`, common causes (parser broke, upstream 500, rate-limit).
- c. **Test a PHP change locally** — `php -S localhost:8000 -t public_html`, how to point it at a dev DB, how to mimic nginx `fastcgi_params`.
- d. **Restore from backup** — which file in `backups/`, exact `sqlite3 .restore` command, `PRAGMA integrity_check` verification, expected row counts.
- e. **Quarterly restore drill checklist** — actually practice (d) on a scratch VM; sign-off date in this file.
*Effort: half day.*

**B2. Fix `calc_rating` N+1.** `src/kayak/cli/calc_rating.py:43-159` — preload `gauge_source` into a dict keyed by `gauge_id` once before the loop. Add a regression test using a SQLAlchemy event listener counting queries. *Effort: 1 hr.*

**B3. Type parser constructors explicitly.** Replace `*args: Any, **kwargs: Any` with explicit keyword signatures matching `BaseParser.__init__` in `src/kayak/parsers/{usgs,usace_outflow,usbr,wa_gov}.py`. *Effort: 1 hr.*

**B4. Parser error-mode tests.** Add `tests/test_parsers/test_error_modes.py` covering: malformed XML/JSON, empty response body, HTTP 500 surface. These are exactly the failures A1 will start exposing, so pre-building the tests hardens the handoff. *Effort: half day.*

**B5. Sort `RatingData` on insert.** `src/kayak/db/data_db.py:560-564` — sort by `gauge_height_ft` before INSERT (or add an assertion). The model comment at `models.py:249-264` says points must be sorted for interpolation but nothing enforces it. *Effort: 30 min.*

**B6. Update `.env.example`.** Add `FETCH_USER_AGENT`, `FETCH_TIMEOUT` with one-line explanations; clarify which vars are required vs. optional. *Effort: 15 min.*

---

## Phase C — Polish & Hygiene (post-handoff is fine)

- **C1.** Extract PHP shared lookups — dedupe `api.php` + `latest.php` into `php/includes/lookups.php`. *1 hr.*
- **C2.** PHP magic-number constants — `php/includes/constants.php` for `MAX_REACHES=200`, sparkline threshold `=60`. *30 min.*
- **C3.** Raise test coverage on `decimate.py:139-256` and `build.py:853-1066` (GeoJSON, sparkline, atomic write, edge cases). *Full day.*
- **C4.** Cut a `0.2.0` release tag and collapse the `Unreleased` section. *15 min.*
- **C5.** Investigate `tpw.tpw` at repo root — add a README comment or `.gitignore` if user-local. Don't just delete. *5 min.*
- **C6.** Tighten CSP `img-src` — replace `https://*.tile.openstreetmap.org` with exact subdomains if tile loading still works. *30 min.*
- **C7.** `conf/security-headers.conf:8` — consider adding `Permissions-Policy:` to match modern hardening baselines. *15 min.*

---

## Explicitly NOT recommended

- **Alembic.** Overkill for a single-deploy, single-developer SQLite system. SQLite's ALTER limitations force Alembic batch-ops for most real changes — extra concept surface area for a junior. A4's ~100-line SQL runner is diffable, trivially reversible, and the junior can read plain SQL from day one. Revisit only if the project goes multi-instance or grows a second developer.
- **SQLite → Postgres.** Fine for this workload (hourly writes, low-QPS reads). Postgres on 2 GB needs real tuning and a new backup story for zero user-visible gain.
- **Rewriting `build.py` or `fetch.py`.** They're complex but load-bearing and tested at integration level. Rewrites here are pure risk. Add targeted tests (C3) if something breaks.
- **PHP HTML-output / snapshot tests.** Churn constantly, give little signal. Manual staging smoke is cheaper.
- **PHP ORM / query builder.** 5 query sites, all prepared. 10× complexity for stylistic gain.
- **`Restart=on-failure` without `StartLimit*`.** Restart storms on systemic outages. A3's burst-limited config avoids this.
- **Removing `allow_negative_flow`** or other "odd" model fields — they exist for specific USBR quirks; leave alone.
- **OPcache preloading** — already implicit via PHP-FPM defaults on Debian; don't chase microseconds unless profiling says so.

---

## Sequencing rationale

A1 is the lynchpin: until pipeline failures actually *fail*, every other fix's effect is invisible in prod. A3's hardening is meaningless before A1 because `Restart=on-failure` needs a non-zero exit. A2 is trivial and independent but bundle with A1 in one PR since both touch "the pipeline locks the DB too long" failure class. A4 (migration runner) must land before A6 — otherwise the new indexes become retroactive drift to explain later. A5 (source-name footgun) is a pure code change, independent of everything else; include it in the A4 PR since it also touches the "source by name" semantics. B1's runbook references the new A1/A3 behavior (journalctl, exit codes, alert sources), so it's written after those land. B4 depends on A1 too — the error modes it tests are only alertable after A1. Phase C is independently shippable; slot into quiet weeks.

---

## Verification

**Phase A**
- A1: unit test asserts `pipeline()` raises/exits non-zero when a step raises; manual: `systemctl start kayak-pipeline.service` after injecting a failing fetch URL → `systemctl status` shows `failed` and `OnFailure=kayak-notify-failure@%n.service` fires.
- A2: `php -r 'require "php/includes/db.php"; $p=get_db(); echo $p->query("PRAGMA busy_timeout")->fetchColumn();'` returns `30000`.
- A3: `systemd-analyze verify systemd/*.service` clean; `systemctl show kayak-pipeline | grep -E 'MemoryMax|Restart|StartLimit'`.
- A4/A6: wipe scratch DB → `init-db` → schema identical to `init-db` on a DB that's been migrated from 0001 (diff `sqlite3 .schema`). `schema_migrations` row present for 0001. `EXPLAIN QUERY PLAN` for a `WHERE source_id=?` query against `latest_observation` shows `USING INDEX` instead of `SCAN`.
- A5: new test in `tests/test_data_db.py` asserts `get_source_by_name(session, 'HCR')` returns a Source rather than raising on a DB with duplicate names. `docs/database-schema.md` has a note explaining why `source.name` is non-unique.
- A7: on a clean nginx install without `security-harden.sh`, `nginx -t && systemctl reload nginx` succeeds.

**Phase B**
- B1: new maintainer walks all 5 scenarios on a fresh VM without asking questions.
- B2: `EXPLAIN QUERY PLAN` shows 1 query instead of N; query-count test asserts ≤ 3 queries per `calc_rating` run.
- B3: `mypy src/kayak/parsers/` clean, no `Any` in constructor signatures.
- B4: new tests in `tests/test_parsers/test_error_modes.py` cover malformed/empty/500; parser coverage ≥ 85 %.
- B5: test inserts rating rows out-of-order → stored rows sorted → `calc_rating` produces expected interpolations.

**Phase C:** standard coverage run; `git tag v0.2.0` after CHANGELOG finalized.

---

## Risks / trade-offs

1. **A1 will surface latent failures that have been silently tolerated.** Mitigation: land A3 (bounded restart) *in the same deploy* so a newly-visible chronic failure caps at 3 alerts/hour, not a pager storm. Dry-run `levels pipeline` manually for a week before enabling the service change to shake out quiet failures.
2. **A4's home-grown migration runner could have bugs the junior can't debug.** Mitigation: keep under 100 LOC, unit-test it, document rollback in B1, keep each migration single-purpose (one DDL change per file).
3. **A5 reframed, not removed.** Original plan recommended `UNIQUE(source.name)`. Verified against the live DB: 9 duplicate rows exist, mostly intentionally (same physical station, multiple fetch endpoints — `HCR`, `29C100`, etc.). The unique constraint is the wrong invariant. Revised A5 instead fixes the footgun in `get_source_by_name`, documents the non-uniqueness, and (optionally, A5a) cleans up the one genuinely dead row. Trade-off: no database-enforced guarantee of single-source-per-name, but none was justified.

---

## Critical files (cite when executing)

- `src/kayak/cli/pipeline.py` — A1
- `php/includes/db.php` — A2
- `systemd/kayak-{pipeline,decimate,backup}.service` — A3
- `src/kayak/cli/init_db.py`, new `src/kayak/cli/migrate.py`, new `data/db/migrations/` — A4
- `src/kayak/db/models.py` — A5, A6
- `deploy/nginx-ratelimit.conf`, `scripts/security-harden.sh`, `conf/levels.nginx` — A7
- `src/kayak/cli/calc_rating.py` — B2
- `src/kayak/parsers/{usgs,usace_outflow,usbr,wa_gov}.py`, `src/kayak/parsers/base.py` — B3
- new `tests/test_parsers/test_error_modes.py` — B4
- `src/kayak/db/data_db.py` — B5
- new `docs/RUNBOOK.md` — B1
- `.env.example` — B6

---

## Reproduce — commands a cross-check session can run on the live system

These commands regenerate every directly-verified claim. Run them on the Debian box; adjust `DB` if needed. All are read-only.

```bash
# Paths — adjust if your live layout differs
DB=${DB:-/home/pat/DB/kayak.db}
REPO=${REPO:-/home/pat/kayak}
cd "$REPO"

# --- Finding 1: pipeline swallows exceptions ---
sed -n '60,90p' src/kayak/cli/pipeline.py
# Expect to see: `except Exception as e:` followed by `logger.error(...)` and no re-raise.

# --- Finding 2: busy_timeout mismatch ---
grep -n busy_timeout src/kayak/db/engine.py php/includes/db.php
# Expect: engine.py -> 30000, php/includes/db.php -> 5000.

# --- Finding 3: Source.name not unique + per-row audit of 9 duplicates ---
sqlite3 "$DB" "SELECT name, COUNT(*) FROM source GROUP BY name HAVING COUNT(*)>1;"
# Expect 7 names: 29C100 (3), 30C070 (3), CSCI (2), HCR (2), LOCO3 (2), NMFO3 (2), WNFO3 (2).

sqlite3 -header -column "$DB" "
SELECT
  s.id, s.name,
  COALESCE(s.agency,'(null)') AS agency,
  COALESCE(fu.parser,'(null)') AS parser,
  substr(COALESCE(fu.url,'(null)'), 1, 70) AS url,
  (SELECT COUNT(*) FROM observation        o  WHERE o.source_id=s.id)  AS obs_rows,
  (SELECT COUNT(*) FROM latest_observation lo WHERE lo.source_id=s.id) AS latest_rows,
  (SELECT COUNT(*) FROM gauge_source       gs WHERE gs.source_id=s.id) AS gauge_links,
  (SELECT group_concat(DISTINCT o.data_type) FROM observation o WHERE o.source_id=s.id) AS data_types
FROM source s
LEFT JOIN fetch_url fu ON s.fetch_url_id = fu.id
WHERE s.name IN ('29C100','30C070','CSCI','HCR','LOCO3','NMFO3','WNFO3')
ORDER BY s.name, s.id;"
# HCR id=935 should show obs_rows=0 and NULL url/parser — that's the dead row.
# 29C100 / 30C070 rows should each emit a distinct data_type (temperature / gauge / flow).

# Confirm no production caller depends on get_source_by_name's single-row semantics
grep -rn 'get_source_by_name' src/ tests/
# Expect hits only in src/kayak/db/data_db.py (definition) and tests/test_data_db.py (tests).

# --- Finding 4: systemd unit lacks hardening ---
grep -nE 'Restart|MemoryMax|CPUQuota|NoNewPrivileges|ProtectSystem|PrivateTmp' \
    systemd/kayak-pipeline.service systemd/kayak-decimate.service systemd/kayak-backup.service
# Expect: no matches (directives absent).

# --- Finding 5: no migration mechanism ---
sed -n '1,110p' src/kayak/cli/init_db.py | grep -nE 'create_all|migrate|migration'
# Expect: only `create_all`.

# --- Finding 6: data/db/ holds CSVs, no migrations dir ---
ls data/db/

# --- Finding 7: edit.php is correctly locked down ---
sed -n '1,80p' php/edit.php
# Expect strict_types, Basic-Auth check, random_bytes CSRF token, hash_equals verification,
# session_set_cookie_params with HttpOnly+SameSite=Strict+secure-on-HTTPS.

# --- Missing-index claims ---
sqlite3 "$DB" "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND tbl_name IN ('gauge_source','reach_state','latest_observation','latest_gauge_observation');"
# Expect: only implicit PK indexes (sqlite_autoindex_*) plus `ix_reach_state_state_id` on reach_state.
# No standalone index on gauge_source(source_id), reach_state(reach_id), latest_observation(source_id),
# latest_gauge_observation(gauge_id).

# --- calc_rating N+1 ---
sed -n '40,165p' src/kayak/cli/calc_rating.py
# Look for: outer loop over gauges with a per-iteration DB call inside.

# --- Parser *args Any forwarding ---
grep -nE 'def __init__\(self, \*args: Any, \*\*kwargs: Any\)' src/kayak/parsers/*.py

# --- RatingData insert not sorting ---
sed -n '550,580p' src/kayak/db/data_db.py
# Expect no `sorted(...)` or ORDER BY before the INSERT.

# --- nginx php zone lives only in hardening script ---
grep -n 'zone=php' conf/levels.nginx deploy/nginx-ratelimit.conf scripts/security-harden.sh 2>/dev/null
# Expect zone=php reference in conf/levels.nginx and only definition in scripts/security-harden.sh.
```

If any of these return unexpected output, stop and investigate before acting — the plan's recommendations assume the above findings. Flag discrepancies back to the author.
