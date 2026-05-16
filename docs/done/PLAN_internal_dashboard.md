# Plan — Internal `/_internal/` dashboard (Phase 2.4)

> **Cross-check:** plan drafted 2026-05-15 against `main` at `07c6184`.
>
> **Iter log:**
> - iter 1 (2026-05-15): initial draft.
> - iter 2 (2026-05-15): verified against `php/includes/auth.php`,
>   `php/login.php`, `tests/php/IntegrationTestCase.php`,
>   `conf/snippets/levels-common.conf`, `conf/sites/levels-*`,
>   `php/csp-report.php`. **5 drifts caught**, plan updated:
>   (a) login redirect query param is `?next=`, NOT `?return=`;
>   (b) auth.php already has `require_maintainer()` — use the helper,
>   don't reinvent the check; non-maintainer signed-in editors get a
>   **403 page** (not a 302 to login);
>   (c) `editor.status` enum is
>   `'pending'|'minimal'|'full'|'maintainer'|'banned'` (per
>   `seedEditorSession` docblock and the editor-feature DDL), NOT
>   `'invited'|'full'|'maintainer'`;
>   (d) CSP log path comes from `Config::str('csp_log_path',
>   '/home/pat/logs/csp.log')` — use the same lookup `csp-report.php`
>   uses; rotation produces `csp.log`, `csp.log.1`, `csp.log.2.gz`, ...
>   (`.gz` rotation, NOT all uncompressed);
>   (e) the shared `levels-common.conf` runs an unbounded
>   `location ~ \.php$ { ... fastcgi_pass ... }` block — that means
>   `/_internal/index.php` would also be served by the wkcc.org +
>   wkcc-test.org vhosts unless explicitly blocked. Plan now adds a
>   `location ^~ /_internal/ { return 404; }` guard to BOTH wkcc
>   vhost files (belt-and-suspenders against future symlink leaks).
> - iter 3 (2026-05-15): verified DB schema + cross-refs. Findings:
>   (a) `latest_observation` PK is `(source_id, data_type)` — a single
>   source has 1+ rows (650 latest_observation rows for 284 sources).
>   Freshness SQL must `GROUP BY s.id` + `MAX(observed_at)` to collapse
>   data-types per source (already correct in plan, but note the why);
>   (b) `source.agency` is nullable — status.php filters
>   `WHERE agency IS NOT NULL`; the dashboard per-source view should
>   render NULL agencies as "—" rather than filter them out (we *want*
>   to see uncategorized sources);
>   (c) `php/status.php:81-99` already computes the per-agency rollup
>   the plan called for under "aggregate counts." Dashboard value-add
>   is per-source granularity. Plan unchanged — the duplication is
>   trivial and a "share helpers" refactor on `status.php` is not
>   worth doing for the MVP. Surface as TBD #6.
>   (d) DB path resolution: use `_sqlite_path()` from
>   `php/includes/db.php:20` (it resolves Config + SQLITE_PATH env);
>   the WAL/SHM sidecars are `<path>-wal` and `<path>-shm`;
>   (e) `schema_migrations` columns: `version TEXT PK`,
>   `applied_at DATETIME`. Schema head = `version` ORDER BY
>   `applied_at DESC LIMIT 1`;
>   (f) `docs/PLAN_production_discipline.md:170` Phase 2.4 still says
>   "basic-auth via htpasswd or IP-allowlist" — that predates the
>   2026-05-15 chat decision to reuse editor_session. Banner update
>   should also rewrite that line.
> - iter 4 (2026-05-15): verified deploy + symlink behavior. Findings:
>   (a) `safe_next_url()` (`auth_magic_link.php:228`) accepts URLs
>   matching `^/[^/\\]` — `/_internal/` qualifies (underscore is not
>   `/` or `\`). The return-from-login redirect will land back on the
>   dashboard rather than `/`;
>   (b) `_deploy_staging_to_live` (`deploy.py:367`) and `_sweep_orphans`
>   (`deploy.py:399`) both skip symlinks — so the public_html
>   docroot-pickup hook is a single **directory symlink**
>   `public_html/_internal -> ../php/_internal/`, NOT a per-file
>   symlink inside a real `_internal/` directory. Cleaner — any
>   future MVP-followup file added under `php/_internal/` (e.g. an
>   API endpoint for refresh) auto-serves with no extra symlink;
>   (c) PHP `__DIR__` resolves through symlinks via realpath, so the
>   in-file `require_once __DIR__ . '/../includes/auth.php'` resolves
>   to `/home/pat/kayak/php/includes/auth.php` — matches the pattern
>   used by every existing `php/*.php` page.
> - iter 5 (2026-05-15): CSP + open_basedir + read-ACL checks; **no
>   new findings**, plan considered converged. Notes for the record:
>   (a) `/etc/nginx/snippets/security-headers.conf` CSP allows
>   `style-src 'self' 'unsafe-inline'` — inline `<style>` in the
>   dashboard is fine; `script-src 'self'` (no inline) means we keep
>   the "color by CSS rule" plan (no inline `onClick=` handlers);
>   (b) `open_basedir` is unset in `/etc/php/8.4/fpm/php.ini` — PHP
>   can read `/home/pat/DB/*` and `/home/pat/logs/*` directly
>   (filesystem ACL controls access);
>   (c) `php/` has default ACLs granting www-data read on new files
>   (per CLAUDE.md), so `php/_internal/index.php` inherits read
>   access at creation; no `setfacl` step needed.
>
> Dates absolute. References `file:line` against current `main`.

## Why

Per `PLAN_production_discipline.md` Phase 2.4 (the last open Tier 2
item). Today the operator must SSH and run shell queries to answer
"is anything weird in the data right now?" — per-source freshness,
DB size, recent CSP violations, etc. The internal dashboard surfaces
that signal in a single browser tab.

**Audience:** the operator (Pat) + future bus-factor partner — both
already have maintainer editor sessions, so reuse that auth band.

**Decisions baked in (chat 2026-05-15):**

1. **Auth: maintainer-only via existing `editor_session`** (no new
   credential). `php/includes/auth.php`'s `current_editor()` returns
   the row; gate on `editor.status === 'maintainer'`.
2. **MVP widget scope** — only widgets backed by existing data; defer
   the ones needing new tables (build durations, audit-flagged
   gauges, fetch-error log) to a follow-up.
3. **URL = `/_internal/`** — matches the plan's original naming;
   underscore prefix signals "site-internal namespace."

## Scope inventory (verified against current `main`)

**Auth helpers** (`php/includes/auth.php`):

- `current_editor(): ?array` — `auth.php:144`. Returns the editor +
  session-join row keyed by the `ed_sess` cookie, or null. Validates
  64-char hex format, session-token sha256, `revoked_at IS NULL`,
  `expires_at > now`, and `e.status != 'banned'`. Memoizes per-request
  via static `$cached`.
- `require_editor(): array` — `auth.php:210`. Redirects to
  `/login.php?next=<urlencoded REQUEST_URI>` if `current_editor()` is
  null; otherwise returns the row.
- `require_maintainer(): array` — `auth.php:222`. Calls
  `require_editor()` (so anonymous → 302 to login), then if logged-in
  editor is not maintainer renders a **403 error page** via
  `render_error_page(403, …)`. Use this — do not reinvent the gate.
- `is_maintainer(?array $ed = null): bool` — `auth.php:203`. Pure
  predicate, no side effects.
- Editor `status` enum (per `seedEditorSession` docblock at
  `tests/php/IntegrationTestCase.php:332`):
  `'pending'|'minimal'|'full'|'maintainer'|'banned'`.
- Login page query param is `?next=` (NOT `?return=`):
  `php/login.php:20` reads `$_GET['next']`, and `require_editor()`
  redirects to `/login.php?next=...` at `auth.php:215`.

**nginx vhost** (`conf/sites/levels-{mousebrains-com,test-wkcc-org,wkcc-org}`):

- Three vhosts share `conf/snippets/levels-common.conf` for most
  location blocks. The shared snippet defines a catch-all
  `location ~ \.php$ { fastcgi_pass ... }` at
  `levels-common.conf:151-165` — meaning any vhost that includes the
  snippet WILL serve `/_internal/index.php` to PHP-FPM unless
  explicitly blocked.
- **The active `/_internal/` block lives in the mousebrains vhost
  file** (`conf/sites/levels-mousebrains-com`), NOT in the shared
  snippet.
- **Both wkcc vhost files** (`levels-wkcc-org`,
  `levels-test-wkcc-org`) gain a `location ^~ /_internal/ { return
  404; }` guard. `^~` overrides the regex `\.php$` match, so even
  direct `/_internal/index.php` requests on wkcc hosts return 404.
- Symlinks in `public_html/` mean docroot is shared across all 3
  vhosts; without the wkcc guard, the file would be reachable on
  three hostnames.

**Existing widgets data** (verified queries against live DB):

- Per-source freshness: `latest_observation` + `source` join (290 sources,
  283 with at least one observation). Same query shape as
  `/status.json`'s sources_by_agency, but per-source granularity.
- DB size: `filesize('/home/pat/DB/kayak.db')` plus `-wal` + `-shm`
  sidecars (WAL-mode).
- Recent CSP violations: log path is `Config::str('csp_log_path',
  '/home/pat/logs/csp.log')` — same lookup `php/csp-report.php:87`
  uses. JSON-per-line. Rotation produces `csp.log`, `csp.log.1`,
  `csp.log.2.gz`, `csp.log.3.gz` (logrotate weekly). PHP-FPM has read
  access (it's the writer). MVP reads current + `csp.log.1` only;
  skip `.gz` to dodge a gzopen() dependency.

**Aggregate counts** (cheap, useful at-a-glance):

- `SELECT count(*) FROM source / gauge / reach / observation /
  latest_observation` — useful sanity-check the build's still alive.
- `stat /home/pat/public_html/index.html` mtime — last successful
  build (same convention `/status.json` uses).

**Constraints to remember:**

- nginx CSP blocks inline scripts/handlers — all JS goes in external
  files. The dashboard's "color by age" can be pure CSS rules
  matching `data-age-bucket` attributes (no JS needed for MVP).
- PHP-FPM lacks `mbstring` — use `strlen`/`substr`, not `mb_*`.
- PHPStan level 8 — narrow `PDO::query()`'s `|false` return.
- `levels build` is idempotent and deploys via per-file rename(2).

## Approach

### Widget layout (single page, top to bottom)

```
# Internal dashboard

Signed in as <email> (maintainer, last seen <ts>). [Logout]

## Build + data freshness
- Last build:     <Oregon.html mtime>           ← from index.html stat
- Last obs (any): <MAX(observed_at)>            ← latest_observation
- DB size:        <bytes>  (WAL: <bytes>, SHM)  ← stat
- Schema head:    <migration version>           ← schema_migrations

## Per-source freshness (color-coded)
| source | agency | last observation | age |
|---|---|---|---|
| <name> | <agency> | <ts> | <"5 min ago"> |  (color: green/yellow/orange/red)

## Recent CSP violations (last 50, last 7 days)
| ts | document | violated | blocked |

## Aggregate counts
| metric | value |
| sources | 290 |
| gauges | 354 |
| reaches | 369 |
| observations (all-time) | 12,345,678 |

## Quick links
- /status.json
- /gauges.html
- /map.html
- Better Stack dashboard (external)
```

### Auth flow

`/_internal/index.php` first thing — reuse the existing helper, do
not roll a custom gate:

```php
require_once __DIR__ . '/../includes/auth.php';
$editor = require_maintainer();   // 302 if anon, 403 page if non-maintainer signed-in
```

`require_maintainer()` (`auth.php:222`):
- Anonymous → `require_editor()` redirects to
  `/login.php?next=<rawurlencode($_SERVER['REQUEST_URI'])>` and
  `exit`s.
- Signed-in but `status != 'maintainer'` → calls
  `render_error_page(403, …)` and `exit`s. Body identifies the
  signed-in email + role.
- Maintainer → returns the editor row (with `session_id`,
  `session_expires_at`, etc.).

No feature-flag gate on the dashboard itself — `require_editor()`
does not call `require_editor_feature()`, so the dashboard is
reachable even on a host where `editor_feature=false`. (Caveat: a
maintainer can't *log in* on such a host because `/login.php` does
call `require_editor_feature()`. Prod has the flag on, so this is a
non-issue.)

### Widget queries

**Per-source freshness:**

```sql
SELECT s.id, s.name, s.agency, MAX(lo.observed_at) AS latest_at
FROM source s
LEFT JOIN latest_observation lo ON lo.source_id = s.id
GROUP BY s.id, s.name, s.agency
ORDER BY (latest_at IS NULL), latest_at ASC
```

GROUP BY collapses `latest_observation`'s per-data_type rows
(`source_id, data_type` PK) into one freshness timestamp per
source. `s.agency` may be NULL — render as "—" in the cell; don't
filter the row out (we want uncategorized sources visible).
Render age-bucket via PHP, set `data-age-bucket="fresh|stale|expired|none"`
on each row. CSS colors the cell background.

**Aggregate counts:** five `SELECT count(*)` queries; trivial.

**DB size:** call `_sqlite_path()` from `php/includes/db.php:20` for
the resolved path, then `filesize()` on `<path>`, `<path>-wal`,
`<path>-shm`. Suppress warnings on missing sidecars (a fresh dev
checkout won't have them) and render "—" instead.

**Schema head:**

```sql
SELECT version FROM schema_migrations
ORDER BY applied_at DESC LIMIT 1
```

**CSP recent:** resolve path via `Config::str('csp_log_path',
'/home/pat/logs/csp.log')`. Open the current log + `.log.1` (if
mtime within 7 days). Skip `.gz` rotation tail. Read last ~200
lines via tail-seek, JSON-decode each, filter to within 7 days,
take last 50. No SQL.

### nginx location blocks

**`conf/sites/levels-mousebrains-com`** — inside the HTTPS server
block, BEFORE the `include /etc/nginx/snippets/levels-common.conf`
line:

```nginx
# Internal-only dashboard — maintainer auth via PHP session; nginx
# adds noindex header so it never ends up in search results.
# `=` exact match has higher precedence than the shared `\.php$`
# regex, so the PHP block below catches /_internal/index.php
# specifically without spilling routing into any new files we add
# later.
location = /_internal/ {
    rewrite ^ /_internal/index.php last;
}

location = /_internal/index.php {
    limit_req zone=php burst=10 nodelay;
    try_files $uri =404;
    fastcgi_pass unix:/run/php/php-fpm.sock;
    fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    include fastcgi_params;
    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header Cache-Control "no-store, private" always;
    include /etc/nginx/snippets/security-headers.conf;
}
```

**`conf/sites/levels-wkcc-org`** AND **`conf/sites/levels-test-wkcc-org`** — add a single guard:

```nginx
# Internal-only dashboard lives on levels.mousebrains.com only.
# `^~` overrides the shared `\.php$` regex, blocking direct
# /_internal/index.php access on this hostname.
location ^~ /_internal/ {
    return 404;
}
```

nginx precedence recap:
1. `=` exact-match wins. (`/_internal/` and `/_internal/index.php`
   on mousebrains.)
2. `^~` prefix wins over regex. (`/_internal/*` 404 on wkcc.)
3. Regex `~ \.php$`. (Default PHP routing in the shared snippet.)
4. Plain prefix `/`. (Catch-all in the shared snippet.)

The mousebrains exact matches and the wkcc `^~` guard never collide
(different vhosts, different server blocks), so no deploy-order
risk.

### Files affected

- **New:**
  - `php/_internal/index.php` — single-page dashboard (~200-250 lines).
  - `public_html/_internal` → **directory symlink** to
    `../php/_internal/` (one symlink, not a per-file one). The build's
    `_sweep_orphans` skips symlinks at `deploy.py:399`, so this
    docroot-pickup hook is durable. Set up via
    `ln -s ../php/_internal public_html/_internal`.
  - `tests/php/InternalDashboardTest.php` — smoke test asserts:
    (i) anonymous → 302 to `/login.php?next=%2F_internal%2F`,
    (ii) `seedEditorSession(…, 'full')` → **403** (require_maintainer's
    error page),
    (iii) `seedEditorSession(…, 'maintainer')` → 200 with body
    containing `Internal dashboard` heading + a section title.
- **Modified:**
  - `conf/sites/levels-mousebrains-com` — add the two location blocks
    (exact match `=` for both URIs).
  - `conf/sites/levels-wkcc-org` — add `^~ /_internal/ { return 404; }`.
  - `conf/sites/levels-test-wkcc-org` — add the same guard.
  - `docs/operations.md` — add a row in the monitoring map referencing
    `/_internal/`.
  - `docs/PLAN_production_discipline.md` — Status banner: 2.4 done,
    Tier 2 complete (modulo T+30 follow-ups).

No DB schema change. No Python change. No CSS deploy change (use
inline `<style>` for one-page dashboard styles, or extend
`src/kayak/web/static/style.css` — TBD in iter 2).

## Edge cases

- **Maintainer session expired mid-page.** Each widget query runs
  in the same PHP request — once we hold the editor row, the page
  renders even if the cookie's session expires during the SQL
  pass. Acceptable; next reload redirects to login.
- **CSP log absent or empty.** ~/logs/csp.log might not exist on a
  fresh dev box. Use `file_exists()` + empty-tolerant read.
- **WAL/SHM sidecars absent.** When SQLite isn't actively in
  WAL-mode (e.g., a fresh checkout), the sidecars don't exist —
  `filesize()` is null. Show "—" instead.
- **Empty `latest_observation`.** Brand-new install — table is empty
  → freshness query returns rows with NULL `latest_at`. Render all
  as "no data yet" (bucket="none").
- **htmlspecialchars on UA strings.** CSP log has UA strings that
  could contain `<` or `&`. Always escape on render.

## Testing approach

- **PHPUnit integration test** (`tests/php/InternalDashboardTest.php`):
  - GET `/_internal/` without a session → assert 302 redirect with
    `Location:` header matching `^/login\.php\?next=%2F_internal%2F`.
  - GET `/_internal/` with `seedEditorSession($email, 'full')` →
    **403** + body contains "only available to the site maintainer"
    (the error-page text in `auth.php:230-234`). NOT a 302 — a
    logged-in non-maintainer hits `render_error_page(403)`.
  - GET `/_internal/` with `seedEditorSession($email, 'maintainer')`
    → 200 + body contains `"Internal dashboard"` heading + one of
    the section titles (e.g., `"Per-source freshness"`).
  - The IntegrationTestCase helper `seedEditorSession($email,
    $status = 'full')` is at `IntegrationTestCase.php:335`; pass
    `'maintainer'` for the third case. Use the returned
    `session_token` as the `ed_sess` cookie via `request()`'s
    `$cookies` arg. CSRF is irrelevant — the dashboard is GET-only.
- **PHPStan level 8 + CS Fixer** — `composer analyse` + `composer fix-check`
  must pass. New code uses the same `status_query()` helper pattern
  from `php/status.php` to narrow `PDO::query()` returns.
- **Manual in-browser smoke** on `levels.mousebrains.com/_internal/`:
  - Visit while logged out → land on login.
  - Magic-link in → land on dashboard with all widgets populated.
  - Confirm `X-Robots-Tag: noindex, nofollow` in response headers.
- **fail2ban check.** The mousebrains vhost's access log already
  has fail2ban watching it; the `/_internal/` paths surface in the
  same log. No fail2ban edit needed; brute-force protection comes
  via the editor login (which is also fail2ban-watched).

## Risk

Low-medium. The auth piece is the load-bearing bit — a bug there
exposes internal data. Mitigations:

- **Re-use the existing maintainer check** (not roll new auth) —
  same code path the editor system has trusted for months.
- **PHP integration test asserts the redirect** for non-maintainers,
  catching regressions in future refactors.
- **nginx adds `X-Robots-Tag: noindex`** so the page never indexes
  even if accidentally exposed.

Other risks:

- **CSP log reading at request time** is O(file size). The log
  rotates weekly and is small in practice (<1 MB); for a hot
  request that's fine. If it grows, switch to reading via tail-N
  via `seek()` from end of file.
- **Widget query latency.** All queries are O(1) or O(small)
  index lookups against well-keyed tables. <100 ms on the live
  host's data volumes; acceptable for an interactive page.

## Decisions (TBD — surface in iter 2+)

1. **Inline `<style>` vs extend `/style.css`?** Recommend inline:
   ~30 lines of CSS, only used on this one page. Avoids inlining
   churn into `src/kayak/web/static/style.css` which everyone
   depends on.
2. **Auto-refresh?** Recommend NO — the operator reloads when
   investigating. A `<meta http-equiv="refresh" content="60">`
   could be added later if needed.
3. **WAL truncation behavior.** Showing WAL size as a separate
   row is useful — if it grows unboundedly, that's a real bug.
   Confirmed: show 3 separate rows (db / wal / shm).
4. **CSP widget window — 7 days?** Plan says "recent." 7 days
   bounds it. Recommend 7d.
5. **Logout link.** Reuse existing `/logout.php` — show as a
   secondary link in the page header.
6. **Reuse status.php helpers?** `php/status.php:81-99` already
   computes per-agency rollup + totals. Dashboard's per-source SQL
   is similar but with more columns. **Recommend NOT refactoring
   for MVP**: extracting a shared helper means touching prod
   status.php for a cosmetic dedup. Revisit if the dashboard
   grows >5 widgets that overlap with status.json.
7. **Banner edit in production-discipline plan.**
   `docs/PLAN_production_discipline.md:170` describes 2.4 as
   "basic-auth or IP-allowlist" — stale. When closing 2.4, rewrite
   that line to "maintainer-auth via editor_session" so the doc
   reflects what shipped.
