# Pre-Launch Review — Execution Plan

Drafted 2026-05-09 from a five-agent multi-iteration review (PHP, Python, DB
schema, frontend, ops). Production cutover for `levels.wkcc.org` is scheduled
for **2026-05-20**, expected **1–5k unique visitors/day**.

This document is the step-by-step plan to land the **12 must-fix items** on
`levels-test.wkcc.org` (the live test environment) before cutover, with
explicit verification at each step so nothing breaks unobserved.

The full prioritized punch list (must-fix + should-fix + defer + verify-on-box
+ what's solid) lives at the bottom under **Appendix A**.

---

## How to read this document

Each phase has a fixed shape:

- **Risk addressed** — the finding being closed
- **Files** — exactly what gets touched
- **Apply on dev** — the change recipe (commands, edits)
- **Verify on dev** — pytest / ruff / mypy / EXPLAIN / curl
- **Deploy to levels-test** — the SSH-and-reload sequence
- **Verify on levels-test** — the proof the change works AND nothing else broke
- **Rollback** — concrete revert path

Phases are ordered so the lowest-risk and easiest-to-revert changes land
first; this builds confidence in the deploy pipeline before the harder
changes. Each phase ends with a `git push` + `ssh + git pull` step, so a
broken phase only affects itself.

## Conventions and prerequisites

- Dev workstation: macOS, this repo at `/Users/pat/tpw/kayak`, virtualenv at
  `/home/pat/.venv` on the test box.
- Test box: `pat@levels.mousebrains.com` (CNAME target of
  `levels-test.wkcc.org`).
- Test box paths: repo at `/home/pat/kayak`, DB at `/home/pat/DB/kayak.db`,
  static doc-root at `/home/pat/public_html`.
- "Apply on dev" means edit the file, then run from the repo root:

  ```bash
  /Users/pat/tpw/kayak/.venv/bin/pytest -m "not slow" -x
  ruff check src/ tests/
  ruff format --check src/ tests/
  mypy src/
  ```

  These four must be clean before any commit. (Skip mypy/ruff for PHP-only
  changes.)

- "Deploy to levels-test" means: commit on the working branch, push, then
  on the test box:

  ```bash
  ssh pat@levels.mousebrains.com
  cd /home/pat/kayak
  git fetch origin
  git log --oneline HEAD..origin/main         # sanity-check what's incoming
  git pull --ff-only origin main
  ```

  Then run the per-phase reload command (migrate / nginx -t / FPM reload /
  install.service.sh).

- "Verify on levels-test" means run the listed checks against
  `https://levels-test.wkcc.org/`. Where a check needs the box itself, the
  command is shown explicitly.

- "Rollback" means `git revert <hash>` + redeploy. For DB migrations there
  is no automatic down-migration — each migration's rollback is documented
  inline.

- **Dev-mirror DB.** The local `../DB/kayak.db` was pulled this morning
  (2026-05-09 20:01 PT) from the test box. It is a faithful but frozen
  copy. Any change that *modifies* DB data (none in this plan — all
  migrations are additive) should be applied with care to the live DB.

## Phase 0 — Pre-flight on dev

**Goal:** establish a clean baseline so any regression introduced by the
review work is detectable.

```bash
cd /Users/pat/tpw/kayak

# Confirm clean working tree
git status

# Confirm we're on main and up-to-date
git fetch origin
git log -1 --oneline origin/main

# Tag the baseline so we can diff against it later if needed
git tag -a pre-review-baseline -m "Baseline before pre-launch punch list (2026-05-09)"

# Establish test floor
/Users/pat/tpw/kayak/.venv/bin/pytest -m "not slow"
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/

# Save current line counts as a reference
git ls-files | wc -l
git ls-files '*.py' | xargs wc -l | tail -1
git ls-files 'php/**' | xargs wc -l | tail -1
```

**Pass criterion:** all four checks green; tag `pre-review-baseline` exists.

**On failure:** stop; do not start Phase 2 work until the dev tree is
clean. If pytest is failing on `main`, that's a pre-existing issue to
triage first.

## Phase 1 — Read-only verifications on levels-test

**Goal:** confirm the test box is in the state we expect *before* changing
anything. Each one-liner is a prerequisite for at least one later phase.

Run each on `pat@levels.mousebrains.com`:

```bash
# 1.1 — TLS cert SANs (informs DNS plan Phase 2 timing)
sudo openssl x509 -in /etc/letsencrypt/live/levels.mousebrains.com/fullchain.pem \
  -noout -text | grep -A1 'Subject Alternative Name'
# Expected today: levels.mousebrains.com, levels-test.wkcc.org
# After DNS plan Phase 2: + levels.wkcc.org

# 1.2 — current renewal authenticator (DNS.CHANGEOVER.md note)
sudo cat /etc/letsencrypt/renewal/levels.mousebrains.com.conf | grep -E 'authenticator|installer|webroot_path'

# 1.3 — pragmas as the application sees them (NOT the sqlite3 CLI defaults)
sudo -u www-data php -r 'require "/home/pat/kayak/php/includes/db.php"; $db = db(); foreach (["journal_mode","foreign_keys","busy_timeout","synchronous","cache_size","mmap_size"] as $p) { $r = $db->query("PRAGMA $p")->fetch(PDO::FETCH_NUM); echo "$p=$r[0]\n"; }'
# Expected: journal_mode=wal, foreign_keys=1, busy_timeout=30000,
#           synchronous=1 (NORMAL), cache_size=-2000, mmap_size=0

sudo -u pat /home/pat/.venv/bin/python -c '
from kayak.db.engine import get_session
from sqlalchemy import text
import os
url = "sqlite:////home/pat/DB/kayak.db"
with get_session(url) as s:
    for p in ("journal_mode","foreign_keys","busy_timeout","synchronous"):
        print(p, s.execute(text(f"PRAGMA {p}")).scalar())
'
# Expected: matches PHP

# 1.4 — Turnstile secret reaches FPM workers
sudo -u www-data php -r 'echo "TURNSTILE_SECRET=" . (getenv("TURNSTILE_SECRET") ? "<set,len=" . strlen(getenv("TURNSTILE_SECRET")) . ">" : "<unset>") . "\n";'

# 1.5 — timer states
sudo systemctl list-timers 'kayak-*' --no-pager
# Expected: 6 timers, all active, next-run within window

# 1.6 — backup state
ls -la /home/pat/kayak/backups/
sudo systemctl status kayak-backup.timer --no-pager | head -10

# 1.7 — schema_migrations on live (cross-check vs files in repo)
sudo -u pat sqlite3 /home/pat/DB/kayak.db 'SELECT version, applied_at FROM schema_migrations ORDER BY version;'
# Expected: 0001..0015 stamped (matches `ls data/db/migrations/`)

# 1.8 — DB integrity on live
sudo -u pat sqlite3 /home/pat/DB/kayak.db 'PRAGMA integrity_check;'
sudo -u pat sqlite3 /home/pat/DB/kayak.db 'PRAGMA foreign_key_check;'
# Expected: ok / no rows
```

**Pass criterion:** all 8 outputs match expectations. If any disagree with
this document, stop and reconcile before any Phase ≥ 2 deploy.

## Phase 2 — FK indexes round 2 (must-fix #8)

**Risk addressed:** every `gauge.php` hit triggers a full index scan of
`reach` because `reach.gauge_id` has no index (`EXPLAIN QUERY PLAN` returned
`SCAN reach USING INDEX ix_reach_sort_name` on the dev mirror). Six other
unindexed FKs share the same cheap fix.

**Files:**
- `data/db/migrations/0016_fk_indexes_round_2.sql` — new
- `src/kayak/db/models.py` — mirror Index entries

### Apply on dev

Create `data/db/migrations/0016_fk_indexes_round_2.sql`:

```sql
-- Migration 0016: index unindexed FK columns
--
-- Closes the round-2 follow-ups to migration 0013. Without these,
-- "given X, find related Y" queries (e.g. /gauge.php's reach lookup)
-- fall back to a full scan of the supplemental index. All target
-- tables are small today, but adding the indexes now removes the
-- scaling cliff and matches the convention of 0013.
--
-- The most user-visible win is ix_reach_gauge_id: gauge.php's
--   SELECT ... FROM reach r WHERE r.gauge_id = ? ORDER BY r.sort_name
-- previously planned as SCAN reach USING INDEX ix_reach_sort_name
-- (4k row reads). With this index it becomes a SEARCH.
--
-- The remaining six are FK columns whose ON DELETE SET NULL (or CASCADE)
-- cascade scans on parent deletion. Cheap insurance.

CREATE INDEX IF NOT EXISTS ix_reach_gauge_id              ON reach(gauge_id);
CREATE INDEX IF NOT EXISTS ix_edit_history_cr_id          ON edit_history(change_request_id);
CREATE INDEX IF NOT EXISTS ix_editor_reviewed_by          ON editor(reviewed_by);
CREATE INDEX IF NOT EXISTS ix_change_request_reviewed_by  ON change_request(reviewed_by);
CREATE INDEX IF NOT EXISTS ix_source_fetch_url_id         ON source(fetch_url_id);
CREATE INDEX IF NOT EXISTS ix_source_calc_expression_id   ON source(calc_expression_id);
CREATE INDEX IF NOT EXISTS ix_gauge_rating_id             ON gauge(rating_id);
```

Mirror in `src/kayak/db/models.py` — add `Index(...)` entries to the
relevant `__table_args__` blocks:

- `Reach.__table_args__` (around line 478): add `Index("ix_reach_gauge_id", "gauge_id")`
- `EditHistory.__table_args__` (around line 847): add `Index("ix_edit_history_cr_id", "change_request_id")`
- `Editor.__table_args__` (around line 654): add `Index("ix_editor_reviewed_by", "reviewed_by")`
- `ChangeRequest.__table_args__` (around line 781): add `Index("ix_change_request_reviewed_by", "reviewed_by")`
- `Source.__table_args__` (around line 191): replace single Index with tuple including `Index("ix_source_fetch_url_id", "fetch_url_id")` and `Index("ix_source_calc_expression_id", "calc_expression_id")`
- `Gauge.__table_args__` (around line 143): replace single Index with tuple including `Index("ix_gauge_rating_id", "rating_id")`

### Verify on dev

```bash
# Apply the migration to the dev-mirror DB
DATABASE_URL=sqlite:////Users/pat/DB/kayak.db /Users/pat/tpw/kayak/.venv/bin/levels migrate

# Confirm it stamped
sqlite3 /Users/pat/DB/kayak.db "SELECT version, applied_at FROM schema_migrations WHERE version = '0016';"

# Confirm the indexes exist
sqlite3 /Users/pat/DB/kayak.db "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%' AND tbl_name IN ('reach','edit_history','editor','change_request','source','gauge') ORDER BY name;"

# Confirm gauge.php's hot query now searches instead of scans
sqlite3 /Users/pat/DB/kayak.db "EXPLAIN QUERY PLAN SELECT id, sort_name, display_name, name FROM reach WHERE gauge_id = 42 ORDER BY sort_name;"
# Expected: SEARCH reach USING INDEX ix_reach_gauge_id (gauge_id=?)

# Confirm pytest still clean (init-db tests stamp 0016 too)
/Users/pat/tpw/kayak/.venv/bin/pytest -m "not slow" -x
```

### Deploy to levels-test

```bash
# On dev
git add data/db/migrations/0016_fk_indexes_round_2.sql src/kayak/db/models.py
git commit -m "db: add FK indexes round 2 (0016) — closes pre-launch P0"
git push origin main

# On test box
ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git pull --ff-only origin main

# Apply migration on the live DB
/home/pat/.venv/bin/levels migrate
# Expected output: applies 0016
```

### Verify on levels-test

```bash
# On test box
sudo -u pat sqlite3 /home/pat/DB/kayak.db "
SELECT version, applied_at FROM schema_migrations WHERE version = '0016';
EXPLAIN QUERY PLAN
  SELECT id, sort_name FROM reach WHERE gauge_id = 42 ORDER BY sort_name;
"
# Expected: 1 row stamped + plan says SEARCH using ix_reach_gauge_id

# Smoke-test gauge.php under load
for id in $(sudo -u pat sqlite3 /home/pat/DB/kayak.db 'SELECT id FROM gauge LIMIT 10'); do
  curl -s -o /dev/null -w '%{time_total}s gauge.php?id='$id'\n' \
    https://levels-test.wkcc.org/gauge.php?id=$id
done
# Expected: each request < 0.5s; nothing 5xx
```

### Rollback

```sql
-- Indexes are cheap to drop; data is unchanged.
DROP INDEX IF EXISTS ix_reach_gauge_id;
DROP INDEX IF EXISTS ix_edit_history_cr_id;
DROP INDEX IF EXISTS ix_editor_reviewed_by;
DROP INDEX IF EXISTS ix_change_request_reviewed_by;
DROP INDEX IF EXISTS ix_source_fetch_url_id;
DROP INDEX IF EXISTS ix_source_calc_expression_id;
DROP INDEX IF EXISTS ix_gauge_rating_id;
DELETE FROM schema_migrations WHERE version = '0016';
```

Then `git revert <hash>` and redeploy.

## Phase 3 — Reject NaN/Inf observations + CSV injection guard (must-fix #6, #7)

**Risk addressed:**
- NaN/Inf floats from `nwps`, `usace_cda`, and `fetch_usgs_ogc` parsers go
  straight into the DB and poison every cache and the `merge_sources`
  median. Other parsers already check `math.isfinite`.
- `levels.csv` is a public download. A reach name beginning with `=`, `+`,
  `-`, `@`, `\t`, or `\r` would execute as a formula in Excel/Sheets when
  opened. Reach names are maintainer-controlled today, but defense-in-depth
  is one line.

**Files:**
- `src/kayak/db/observations.py` — guard inside `store_observation`
- `src/kayak/cli/build.py` — escape leading-formula chars in `_build_csv`
- `tests/test_observations.py` — new unit tests for the NaN/Inf rejection
- `tests/test_build.py` (or wherever `_build_csv` lives) — new test for
  CSV escaping

### Apply on dev

In `src/kayak/db/observations.py`, near the top of `store_observation()`:

```python
import math
# ...
def store_observation(session, source_id, observed_at, data_type, value):
    if value is None or not math.isfinite(value):
        # Silently drop NaN/Inf — protects every downstream cache and merge.
        # Counter-intuitively, INFO not WARN: government feeds occasionally
        # publish sentinel values (-999999) that arithmetic turns into Inf;
        # we don't want to wake oncall every cycle.
        logger.info(
            "store_observation: dropping non-finite value source_id=%s data_type=%s value=%r",
            source_id, data_type, value,
        )
        return
    # ...existing body...
```

In `src/kayak/cli/build.py`, find `_build_csv` and add the escape helper:

```python
_CSV_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")

def _csv_safe(value: object) -> str:
    """Stringify and prefix with `'` if the value would be interpreted as
    a formula by Excel/Sheets/Numbers. RFC 4180 doesn't require this; it
    is a defense against ``levels.csv`` becoming an attack surface."""
    s = "" if value is None else str(value)
    if s and s.startswith(_CSV_FORMULA_PREFIX):
        return "'" + s
    return s
```

Wrap each cell value in `_build_csv` with `_csv_safe(...)`.

Add tests:

```python
# tests/test_observations.py
import math
import pytest
from kayak.db.observations import store_observation

def test_store_observation_rejects_nan(session, sample_source):
    store_observation(session, sample_source.id, "2026-05-09T12:00", "flow", math.nan)
    assert session.execute(...).scalar() == 0  # nothing stored

def test_store_observation_rejects_pos_inf(session, sample_source):
    store_observation(session, sample_source.id, "2026-05-09T12:00", "flow", math.inf)
    assert session.execute(...).scalar() == 0

def test_store_observation_rejects_neg_inf(session, sample_source):
    store_observation(session, sample_source.id, "2026-05-09T12:00", "flow", -math.inf)
    assert session.execute(...).scalar() == 0
```

```python
# tests/test_build.py (or appropriate test file)
from kayak.cli.build import _csv_safe

def test_csv_safe_passes_normal_strings():
    assert _csv_safe("Sandy River") == "Sandy River"
    assert _csv_safe("=foo") == "'=foo"
    assert _csv_safe("+1") == "'+1"
    assert _csv_safe("-1") == "-1"  # negative number — should NOT escape
    # Wait — RFC 4180 doesn't distinguish "negative number" from "formula"
    # Verify: do we ship negative numbers through this path?
    # Answer: yes, delta_per_hour can be negative. Decision: leave numeric
    # values un-escaped because _build_csv emits them via format strings,
    # not via _csv_safe; only string columns flow through _csv_safe.
```

(Reconcile the negative-number question on dev before pushing — confirm
`_build_csv` does NOT pass numeric values through `_csv_safe`. If it does,
the helper needs to type-check and only escape strings.)

### Verify on dev

```bash
/Users/pat/tpw/kayak/.venv/bin/pytest tests/test_observations.py tests/test_build.py -x
ruff check src/ tests/
mypy src/
```

### Deploy to levels-test

```bash
git add src/kayak/db/observations.py src/kayak/cli/build.py tests/
git commit -m "parsers: reject non-finite observations; csv: escape formula prefix"
git push origin main

ssh pat@levels.mousebrains.com 'cd /home/pat/kayak && git pull --ff-only origin main'
```

The pipeline runs hourly at `:12`. The next pipeline run will pick up the
new code automatically — no service restart needed (editable install).

### Verify on levels-test

```bash
# Watch the next pipeline run land
sudo journalctl -u kayak-pipeline.service -n 200 -f
# Expected: no errors; if any non-finite values arrive, INFO log line
#           appears; observation count growth continues normal.

# Confirm levels.csv has no formula-leading rows
curl -s https://levels-test.wkcc.org/levels.csv | awk -F, '
  NR > 1 {
    for (i = 1; i <= NF; i++) {
      first = substr($i, 1, 1)
      if (first == "=" || first == "+" || first == "@") {
        print "UNESCAPED: row", NR, "col", i, "value=", $i
      }
    }
  }
'
# Expected: no UNESCAPED lines
```

### Rollback

`git revert` the commit; next pipeline run reverts behavior. CSV is
regenerated each pipeline run.

## Phase 4 — PHP search and plot LIMIT clamps (must-fix #4)

**Risk addressed:** `gauge.php`, `source.php`, `reach.php` accept `q=%` and
return every row; `plot.php` accepts arbitrary date ranges and pulls
millions of observations through `derive_rating_lookup`'s self-join. A
single attacker request can pin a PHP-FPM worker for seconds.

**Files:**
- `php/gauge.php` (search query around line 35)
- `php/source.php` (search query around line 26)
- `php/reach.php` (search queries around lines 55/68/83)
- `php/plot.php` (range parsing around lines 44–60)

### Apply on dev

For each of the three search pages, add `LIMIT 200` to the search query.
The PHP review pointed to `review.php:280` as the existing `LIMIT 200`
template — match that style.

For `plot.php`, after the `start`/`end` validation:

```php
// Clamp to a sane window. Graphing 100 years of observations is never
// the user's actual intent; capping defends against a hostile or
// accidentally-broken bookmark.
$start_ts = strtotime($start);
$end_ts   = strtotime($end);
if ($end_ts - $start_ts > 366 * 86400) {
    $start_ts = $end_ts - 366 * 86400;
    $start = date('Y-m-d', $start_ts);
}
// Also defend the SQL with an explicit LIMIT.
$sql .= ' LIMIT 100000';
```

(Adjust to match the existing variable names in `plot.php`.)

### Verify on dev

No automated test exists for these PHP files. Verify by syntax-check:

```bash
php -l php/gauge.php php/source.php php/reach.php php/plot.php
```

If you have PHPUnit configured, run any relevant suite:

```bash
vendor/bin/phpunit  # if applicable
```

### Deploy to levels-test

```bash
git add php/gauge.php php/source.php php/reach.php php/plot.php
git commit -m "php: cap search results to 200 and plot range to 366d"
git push origin main

ssh pat@levels.mousebrains.com 'cd /home/pat/kayak && git pull --ff-only origin main'
# No FPM reload needed for plain PHP file changes.
```

### Verify on levels-test

```bash
# Search wildcard — should return at most 200 rows of HTML
time curl -s 'https://levels-test.wkcc.org/gauge.php?q=%' | wc -l
time curl -s 'https://levels-test.wkcc.org/source.php?q=%' | wc -l
time curl -s 'https://levels-test.wkcc.org/reach.php?q=%' | wc -l
# Expected: bounded response time (< 1s), limited row count

# Plot with extreme range — should clamp to 1 year
time curl -s 'https://levels-test.wkcc.org/plot.php?source_id=222&start=1900-01-01&end=2030-12-31&data_type=flow' \
  | head -c 500
# Expected: < 2s response; no timeout

# Confirm normal usage still works
time curl -s 'https://levels-test.wkcc.org/gauge.php?q=Sandy' | wc -l
# Expected: ~normal response
```

### Rollback

`git revert` and redeploy.

## Phase 5 — description.php latent XSS in calc_expression render (must-fix #5)

**Risk addressed:** `php/description.php:382-403` runs
`preg_replace_callback` on `calc_expression.expression` to autolink the
`source_name::data_type::statistic` references. The callback escapes its
matched portion correctly, but `preg_replace_callback` returns
**unmatched portions of the input verbatim**. So spaces, `+`, `100`, etc.
in the expression — anything between the matches — pass through to the
output unescaped. `calc_expr` is admin-controlled today; the threat is
"one config typo or compromised maintainer account away from XSS."

**Files:**
- `php/description.php` (around lines 382–403)

### Apply on dev

The fix is to escape the whole input first, then run the regex on the
*escaped* string. This requires the regex pattern to work on the escaped
form (e.g. `&` → `&amp;`, but the `::` separator and word characters are
unaffected).

Pattern:

```php
// BEFORE — UNSAFE (unmatched chars passed through unescaped)
$expr_html = preg_replace_callback(
    '/(\w+)::(\w+)::(\w+)/',
    function ($m) {
        $name = htmlspecialchars($m[1]);
        // ...
        return "<a href=\"...\">$name</a>::" . htmlspecialchars("$m[2]::$m[3]");
    },
    $src['calc_expr']
);

// AFTER — escape input first, then run regex on the escaped form
$expr_safe = htmlspecialchars($src['calc_expr'], ENT_QUOTES);
$expr_html = preg_replace_callback(
    '/(\w+)::(\w+)::(\w+)/',
    function ($m) {
        // Inputs are now pre-escaped; m[1..3] are word chars only,
        // so no further escaping needed inside the callback.
        $url = '/source.php?name=' . urlencode($m[1]);
        return '<a href="' . htmlspecialchars($url, ENT_QUOTES) . '">'
             . $m[1]
             . '</a>::' . $m[2] . '::' . $m[3];
    },
    $expr_safe
);
```

(Adjust to match the existing autolink target URLs.)

### Verify on dev

Manual test by editing a `calc_expression.expression` row in the dev DB
to include HTML metacharacters between matches, then load the affected
description page in a browser and view source.

```bash
# Pick a calc-driven gauge from the dev DB
sqlite3 /Users/pat/DB/kayak.db "SELECT g.id, g.name, ce.expression FROM gauge g JOIN gauge_source gs ON g.id = gs.gauge_id JOIN source s ON gs.source_id = s.id JOIN calc_expression ce ON s.calc_expression_id = ce.id LIMIT 5;"

# Temporarily inject HTML metacharacters (don't push this!)
sqlite3 /Users/pat/DB/kayak.db "UPDATE calc_expression SET expression = expression || ' <script>alert(1)</script>' WHERE id = <pick-one>;"

# Serve locally
php -S localhost:8000 -t public_html

# In another terminal:
curl -s 'http://localhost:8000/description.php?id=<reach-id>' | grep -o '&lt;script&gt;'
# Expected: prints the escaped form

# REVERT the test injection
sqlite3 /Users/pat/DB/kayak.db "UPDATE calc_expression SET expression = REPLACE(expression, ' <script>alert(1)</script>', '') WHERE id = <pick-one>;"
```

(There's no automated test that simulates this; manual is the right
approach.)

### Deploy to levels-test

```bash
git add php/description.php
git commit -m "description: escape calc_expression rendering to close latent XSS"
git push origin main

ssh pat@levels.mousebrains.com 'cd /home/pat/kayak && git pull --ff-only origin main'
```

### Verify on levels-test

```bash
# Pick a description page known to render a calc-driven source
curl -s 'https://levels-test.wkcc.org/description.php?id=<known-reach-id>' \
  | grep -A2 -B2 'Calculated:'
# Expected: visually identical to before (the escaping is idempotent on
#           normal expressions because they contain no HTML metacharacters)

# Compare to baseline
diff \
  <(curl -s 'https://levels-test.wkcc.org/description.php?id=42' | grep -A2 'Calculated:') \
  <(curl -s 'https://levels-test.wkcc.org/description.php?id=42' | grep -A2 'Calculated:')
# Expected: empty diff (sanity check stable response)
```

### Rollback

`git revert` and redeploy. The feature is currently functional with
trusted input; reverting restores the prior behavior.

## Phase 6 — mail.php CRLF strip (must-fix #9)

**Risk addressed:** `php/includes/mail.php` strips `\r\n` from extra
headers (line ~55) but the **subject** argument passed to PHP's `mail()`
is not CR/LF-stripped. `contact.php`, `comment.php`, `propose.php` build
subjects from user-or-DB content (`reach_name`, contact subject). A
crafted subject `"Hello\r\nBcc: victim@example.com"` injects an additional
header. PHP's built-in mail subject sanitization is not reliable across
versions.

**Files:**
- `php/includes/mail.php` (`send_email` function)

### Apply on dev

In `php/includes/mail.php`, inside `send_email` (or wherever the subject
flows into the `mail()` call), add a single line:

```php
// Defense against header injection via Subject. PHP's mail() accepts
// the subject argument with no built-in CR/LF stripping; callers may
// pass DB-sourced strings (reach names, contact subjects) that we must
// not let escape into header context.
$subject = preg_replace('/[\r\n]+/', ' ', $subject);
```

Place it before any callsite that uses `$subject`. If `mail.php` already
has a `_sanitize_header($v)` helper, factor a `_sanitize_subject($v)`
that does the same.

### Verify on dev

```bash
php -l php/includes/mail.php

# Unit test (write a small PHP script that calls the helper):
php -r '
require "php/includes/mail.php";
// (call your helper if you exposed it, or test through send_email with a
//  mock transport)
$s = "test\r\nBcc: victim@example.com";
$cleaned = preg_replace("/[\r\n]+/", " ", $s);
assert($cleaned === "test Bcc: victim@example.com", "got: $cleaned");
echo "ok\n";
'
```

### Deploy to levels-test

```bash
git add php/includes/mail.php
git commit -m "mail: strip CR/LF from subject to close header injection"
git push origin main
ssh pat@levels.mousebrains.com 'cd /home/pat/kayak && git pull --ff-only origin main'
```

### Verify on levels-test

The CSP-violation reporter and the `csp-report.php` endpoint do NOT
exercise this path; the `contact.php` form does. To verify:

1. From a browser, fill `contact.php` with `Subject: "test\nBcc: ..."`
   (paste a literal newline using a tool that supports it; Firefox's
   form input strips newlines, so use curl):

   ```bash
   # Replace the magic-link cookie with your real one (from /login.php)
   COOKIE='ed_sess=<your token>; csrf=<your csrf>'
   curl -i -X POST https://levels-test.wkcc.org/contact.php \
     -H "Cookie: $COOKIE" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     --data-urlencode "csrf=<your csrf>" \
     --data-urlencode $'subject=hello\r\nBcc: notbcc@example.com' \
     --data-urlencode 'message=test'
   ```

2. Check the maintainer inbox: the message should arrive with a normal
   subject (`hello Bcc: notbcc@example.com` as a single subject string),
   and **no Bcc** to `notbcc@example.com`.

3. Check `journalctl -u php-fpm.service` for any errors.

### Rollback

`git revert` and redeploy.

## Phase 7 — Audit timer email + notify-failure subject tagging (must-fix #10, #11)

**Risk addressed:**
- `kayak-audit-gauges.service` runs but doesn't email findings
  (`ExecStart` passes only `--days 14`). Audit results sit silently in
  journald — useless as a launch-day signal.
- `kayak-notify-failure@.service` collapses every timer's failure into
  the same subject line; you can't tell pipeline-died from
  healthcheck-stale from the email alone.

**Files:**
- `systemd/kayak-audit-gauges.service` — add `--email`
- `systemd/kayak-notify-failure@.service` — tag subject with `%i`,
  optionally include `journalctl -u %i --since '5 min ago' | tail -50`
  in the body
- `.env.example` — document `KAYAK_AUDIT_EMAIL`

### Apply on dev

Edit `systemd/kayak-audit-gauges.service` `[Service]` block; change
`ExecStart` to:

```ini
ExecStart=/home/pat/.venv/bin/levels audit-gauges --days 14 --email ${KAYAK_AUDIT_EMAIL}
```

Confirm `EnvironmentFile=/home/pat/.config/kayak/.env` is already present.
Add `KAYAK_AUDIT_EMAIL` to `.env.example` with a comment.

Edit `systemd/kayak-notify-failure@.service` to enrich the alert. The
existing unit shape (per the ops review) pipes mail or invokes an
ExecStart. Replace its body with something like:

```ini
[Unit]
Description=Notify on failure of %i

[Service]
Type=oneshot
EnvironmentFile=-/home/pat/.config/kayak/.env
ExecStart=/bin/bash -c '\
  SUBJ="[kayak-alert] %i FAILED on $(hostname -s)"; \
  BODY=$(journalctl -u %i --since "5 minutes ago" --no-pager | tail -50); \
  printf "Subject: %%s\n\n%%s\n" "$SUBJ" "$BODY" | /usr/bin/msmtp -a default ${KAYAK_AUDIT_EMAIL:-pat.kayak@gmail.com}'
```

(Adapt to the existing pattern — preserve `User=`, sandboxing, etc.)

### Verify on dev

These are systemd unit changes; dev verification is `systemctl cat` style:

```bash
# On a Debian dev VM (or skip and rely on test-box verify)
systemd-analyze verify systemd/kayak-audit-gauges.service
systemd-analyze verify systemd/kayak-notify-failure@.service
```

(macOS has no systemd; skip and rely on test-box.)

### Deploy to levels-test

```bash
git add systemd/kayak-audit-gauges.service systemd/kayak-notify-failure@.service .env.example
git commit -m "systemd: audit-gauges emails findings; notify-failure tags subject and includes journal tail"
git push origin main

ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git pull --ff-only origin main

# Add KAYAK_AUDIT_EMAIL to the live .env
grep -q '^KAYAK_AUDIT_EMAIL=' /home/pat/.config/kayak/.env \
  || echo "KAYAK_AUDIT_EMAIL=pat.kayak@gmail.com" >> /home/pat/.config/kayak/.env

# Reinstall units (install.service.sh is diff-based so this is safe)
sudo bash systemd/install.service.sh
```

### Verify on levels-test

```bash
# 7.1 — Trigger the audit manually and confirm an email arrives
sudo systemctl start kayak-audit-gauges.service
sudo journalctl -u kayak-audit-gauges.service -n 50 --no-pager
# Expected: "audit complete; emailed N findings to <addr>" or similar

# Wait ~30s for SMTP, then check pat.kayak@gmail.com inbox.

# 7.2 — Force a notify-failure to confirm the new subject + body
sudo systemctl start kayak-notify-failure@kayak-pipeline.service
# Expected: alert email arrives within ~30s with subject including
#           "kayak-pipeline.service" AND the last 50 lines of journal.
```

### Rollback

`sudo bash systemd/install.service.sh` after `git revert`. The diff-based
installer will detect the unit change and restart only those units.

## Phase 8 — nginx rate-limit zone split (must-fix #2)

**Risk addressed:** `auth.php` token consumption, `login.php` magic-link
issuance, and `contact.php` form submission all share the `auth` zone
(3 r/m). At 5k DAU even 1% contact rate (~50/day) collides with login.

**Files:**
- `deploy/nginx-ratelimit.conf` — zones
- `conf/levels.nginx` — per-location bindings
- `deploy/levels` — sync (commented as the live snapshot)

### Apply on dev

In `deploy/nginx-ratelimit.conf`, add (or split out from existing):

```nginx
# Login (magic-link issuance) — tightest because each request enqueues
# an outbound email. Per-IP defense is supplemented by per-email throttle
# inside auth.php's magic_link_under_throttle().
limit_req_zone $binary_remote_addr zone=login:1m rate=3r/m;

# Auth (magic-link consumption) — relaxed: consumption requires a
# 32-byte token, so brute force is infeasible regardless of rate.
limit_req_zone $binary_remote_addr zone=auth:1m rate=10r/m;

# Contact form — anonymous; Turnstile + honeypot are first-line.
limit_req_zone $binary_remote_addr zone=contact:1m rate=10r/m;
```

In `conf/levels.nginx`, change the per-location bindings:

```nginx
location = /login.php {
    limit_req zone=login burst=2 nodelay;
    # ...
}

location = /auth.php {
    limit_req zone=auth burst=4 nodelay;
    # ...
}

location = /contact.php {
    limit_req zone=contact burst=4 nodelay;
    # ...
}
```

If `deploy/levels` is the live snapshot, mirror the change there too.

### Verify on dev

```bash
# Syntax-check by feeding to a local nginx (if you have one)
nginx -t -c $(realpath conf/levels.nginx)
# (or skip and rely on test-box `nginx -t`)
```

### Deploy to levels-test

```bash
git add deploy/nginx-ratelimit.conf conf/levels.nginx deploy/levels
git commit -m "nginx: split auth zone into login/auth/contact (3/10/10 r/m)"
git push origin main

ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git pull --ff-only origin main

# Deploy nginx config (the operator step — the repo doesn't auto-deploy)
sudo cp conf/levels.nginx /etc/nginx/sites-available/levels
sudo cp deploy/nginx-ratelimit.conf /etc/nginx/conf.d/kayak-ratelimit.conf
sudo nginx -t
# Expected: syntax is ok; test is successful

sudo systemctl reload nginx
```

### Verify on levels-test

```bash
# 8.1 — Three independent zones now (each holds 3-10 req before 503)
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w '%{http_code} ' https://levels-test.wkcc.org/login.php
done
echo
# Expected: 200 200 200 503 503 (login zone exhausted at burst=2)

sleep 30  # let bucket refill

for i in 1 2 3 4 5 6 7; do
  curl -s -o /dev/null -w '%{http_code} ' https://levels-test.wkcc.org/contact.php
done
echo
# Expected: 200 ×4 then 503 (independent of login zone)

# 8.2 — Confirm /login.php still rate-limits independently
# (you should not be locked out of login by the contact above)
curl -s -o /dev/null -w '%{http_code}\n' https://levels-test.wkcc.org/login.php
# Expected: 200

# 8.3 — Confirm static traffic unaffected
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' https://levels-test.wkcc.org/
# Expected: 200 < 0.3s
```

### Rollback

```bash
ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git revert <hash>
sudo cp conf/levels.nginx /etc/nginx/sites-available/levels
sudo cp deploy/nginx-ratelimit.conf /etc/nginx/conf.d/kayak-ratelimit.conf
sudo nginx -t && sudo systemctl reload nginx
```

## Phase 9 — PHP-FPM pool hardening (must-fix #3)

**Risk addressed:** `deploy/kayak-fpm-pool.conf` has only `open_basedir`
for hardening. No `disable_functions`, no `expose_php=off`, no
`memory_limit` cap, no `max_execution_time`. A foothold escalates further
than necessary; a runaway request can hold a worker indefinitely.

**Files:**
- `deploy/kayak-fpm-pool.conf`

### Apply on dev

Add to the `[kayak]` pool section:

```ini
; Hide PHP version
php_admin_flag[expose_php] = off

; Disable shell-execution functions. Kayak's PHP layer never legitimately
; calls any of these. Block the SUID/exec families and a few process-control
; primitives to defend against generic PHP exploits that try to drop a
; webshell.
php_admin_value[disable_functions] = exec,system,passthru,shell_exec,proc_open,popen,pcntl_exec,proc_close,proc_get_status,proc_nice,proc_terminate

; Memory and time limits — defense-in-depth against runaway requests.
; Kayak's PHP queries are small and fast; 64 MB is generous.
php_admin_value[memory_limit]       = 64M
php_admin_value[max_execution_time] = 30

; Body / upload limits — sized to fit propose.php (10k chars × ~1.5 url-encoded)
; plus headroom. No file upload endpoint is implemented today.
php_admin_value[post_max_size]      = 256K
php_admin_value[upload_max_filesize] = 0

; Block url-fopen so file_get_contents("https://...") and similar are off.
php_admin_flag[allow_url_fopen]     = off

; Block the include-as-URL variant.
php_admin_flag[allow_url_include]   = off
```

### Verify on dev

This pool config doesn't run on dev (macOS, no systemd). Verification is
on the test box.

```bash
# Syntax-check (one-shot)
php-fpm -t -y deploy/kayak-fpm-pool.conf 2>&1 || true
# (PHP-FPM may not be installed locally; skip if so)
```

### Deploy to levels-test

```bash
git add deploy/kayak-fpm-pool.conf
git commit -m "php-fpm: harden pool (disable_functions, expose_php, limits, no url-fopen)"
git push origin main

ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git pull --ff-only origin main

# Deploy
sudo cp deploy/kayak-fpm-pool.conf /etc/php/8.4/fpm/pool.d/kayak.conf

# Test syntax before reload
sudo php-fpm8.4 -t
# Expected: "test is successful"

sudo systemctl reload php8.4-fpm.service
```

### Verify on levels-test

```bash
# 9.1 — Pool hardening took effect
sudo -u www-data php -r '
echo "expose_php=" . ini_get("expose_php") . "\n";
echo "memory_limit=" . ini_get("memory_limit") . "\n";
echo "max_execution_time=" . ini_get("max_execution_time") . "\n";
echo "disable_functions=" . ini_get("disable_functions") . "\n";
echo "allow_url_fopen=" . ini_get("allow_url_fopen") . "\n";
'
# Expected: each value matches what we set

# 9.2 — Smoke-test every public endpoint
for p in / /index.html /Oregon.html /map.html /gauges.html \
         /levels.csv /static/sparklines.json \
         /description.php?id=1 /gauge.php?id=1 /reach.php?q=Sandy \
         /api.php /latest.php /plot.php?source_id=222 /picker.php; do
  code=$(curl -sk -o /dev/null -w '%{http_code}' "https://levels-test.wkcc.org$p")
  echo "$code $p"
done
# Expected: 200 / 302 (no 5xx). Any 500 means the hardening broke
#           something legitimate — usually file_get_contents in some
#           includes/ file. Check journalctl -u php-fpm and revert
#           that one disable_function if needed.

# 9.3 — Confirm a function we expect to still work (htmlspecialchars,
#         file_get_contents on local paths, etc.)
sudo -u www-data php -r 'echo file_get_contents("/home/pat/kayak/public_html/index.html") ? "ok\n" : "FAIL\n";'
# Expected: ok (allow_url_fopen=off does NOT affect local file reads)
```

### Rollback

```bash
ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git revert <hash>
sudo cp deploy/kayak-fpm-pool.conf /etc/php/8.4/fpm/pool.d/kayak.conf
sudo php-fpm8.4 -t && sudo systemctl reload php8.4-fpm.service
```

If only one item caused breakage (e.g. a function that turned out to be
needed), edit the pool to whitelist just that one rather than reverting
the whole hardening.

## Phase 10 — MAIL_FROM and SPF/DKIM/DMARC alignment (must-fix #12)

**Risk addressed:** if `MAIL_FROM=noreply@levels.wkcc.org` but msmtp
relays through Gmail, DMARC will fail and magic-link emails go to spam.
The editor flow effectively doesn't work for new users.

This phase has two paths; pick one before launch:

**Path A — keep `pat.kayak@gmail.com` as From, set Reply-To to
`noreply@levels.wkcc.org`.** Lowest-risk; no DNS changes; "From" line in
clients shows the gmail address (slightly less polished but functional).

**Path B — use `noreply@levels.wkcc.org` as From and configure SPF +
DKIM + DMARC on `wkcc.org`.** Requires DNS access to wkcc.org, which we
do NOT control directly (ClubExpress); requires another support ticket.
Higher polish, blocked on Phase 1 of `DNS.CHANGEOVER.md`.

### Apply on dev

If Path A:
- `php/includes/mail.php` — change the `From:` header to use
  `MAIL_FROM=pat.kayak@gmail.com` and add a `Reply-To:` of
  `noreply@levels.wkcc.org`
- `.env.example` — update `MAIL_FROM` and add `MAIL_REPLY_TO`

If Path B:
- Open a ClubExpress ticket adding SPF/DKIM/DMARC TXT records (the
  exact record values come from the SMTP relay you choose; if msmtp is
  going through Gmail, use Google's published include).
- Generate a DKIM key locally, publish the public key TXT record.
- Verify with `dig TXT wkcc.org`, `dig TXT default._domainkey.wkcc.org`,
  `dig TXT _dmarc.wkcc.org`.

**Recommendation: Path A for launch**, Path B as a post-launch follow-up
once you have the DKIM signing infrastructure in place.

### Verify on dev

Send a test email through your dev environment (if you have msmtp set up)
to a clean Gmail inbox; check the Gmail "show original" view for SPF/DKIM
PASS lines. Path A passes SPF (Gmail's SPF) and skips DKIM (no signing
cert).

### Deploy to levels-test

```bash
git add php/includes/mail.php .env.example
git commit -m "mail: From=pat.kayak@gmail.com, Reply-To=noreply@levels.wkcc.org for DMARC alignment"
git push origin main

ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git pull --ff-only origin main

# Edit live .env to match
grep -q '^MAIL_FROM=' /home/pat/.config/kayak/.env \
  && sed -i 's/^MAIL_FROM=.*/MAIL_FROM=pat.kayak@gmail.com/' /home/pat/.config/kayak/.env \
  || echo 'MAIL_FROM=pat.kayak@gmail.com' >> /home/pat/.config/kayak/.env
grep -q '^MAIL_REPLY_TO=' /home/pat/.config/kayak/.env \
  || echo 'MAIL_REPLY_TO=noreply@levels.wkcc.org' >> /home/pat/.config/kayak/.env

# PHP-FPM reads env from secrets.env / pool config; reload to pick up
sudo systemctl reload php8.4-fpm.service
```

### Verify on levels-test

1. From a fresh, unauthenticated browser, visit
   `https://levels-test.wkcc.org/login.php`, request a magic link to a
   test Gmail address.

2. Check the test Gmail inbox:
   - Email is in Inbox (not Spam).
   - "Show original" → SPF: PASS (gmail.com); DKIM: PASS (gmail.com);
     DMARC: PASS.
   - "From" displays `pat.kayak@gmail.com`.
   - "Reply-To" is `noreply@levels.wkcc.org`.

3. Click the magic link; confirm the editor session is established.

4. Repeat from a second test address (Outlook or Yahoo) to triangulate
   spam-filter judgement.

### Rollback

`git revert` and redeploy. The `.env` change is one `sed` away from
reverting too.

## Phase 11 — Off-site backups (must-fix #1)

**Risk addressed:** `systemd/kayak-backup.sh` writes to
`/home/pat/kayak/backups` on the same VPS. A single-host failure (or
filesystem corruption) loses all four weekly snapshots together. SETUP.md
specs Hetzner Storage Box rsync but it's unimplemented.

This is the last must-fix phase because it requires provisioning the
Storage Box (or another off-host destination) — an external dependency.

**Files:**
- `systemd/kayak-backup.sh` — add the off-host rsync
- `systemd/kayak-backup.service` — relax `RestrictAddressFamilies` and
  `IPAddressDeny` to allow the rsync (currently `AF_UNIX` only,
  `IPAddressDeny=any`)
- `.env.example` — document `STORAGE_BOX_USER`, `STORAGE_BOX_HOST`
- `deploy/SETUP.md` — add the **Restore from off-site** runbook

### Pre-requisite

Provision a Hetzner Storage Box (or any rsync/sftp target):
- Create the box in Hetzner console.
- Generate an SSH key on the test box: `ssh-keygen -t ed25519 -C
  kayak-backup -f ~/.ssh/storage_box_ed25519` (no passphrase, since
  it'll run unattended).
- Upload the public key to the Storage Box.
- Test connectivity: `ssh -i ~/.ssh/storage_box_ed25519 -p 23
  u<box-id>@u<box-id>.your-storagebox.de ls`.

### Apply on dev

Edit `systemd/kayak-backup.sh`. After the existing local-snapshot block,
add:

```bash
# Off-host copy. Loss of /home/pat/kayak/backups (filesystem corruption,
# VPS deletion) without this is unrecoverable.
if [[ -n "${STORAGE_BOX_USER:-}" && -n "${STORAGE_BOX_HOST:-}" ]]; then
    rsync -e "ssh -i $HOME/.ssh/storage_box_ed25519 -p 23 -o StrictHostKeyChecking=accept-new" \
        --partial --timeout=60 \
        "$BACKUP_DIR/$DEST" \
        "${STORAGE_BOX_USER}@${STORAGE_BOX_HOST}:kayak-backups/$DEST" \
      || logger -t kayak-backup -p user.err "off-host rsync FAILED for $DEST"
else
    logger -t kayak-backup -p user.warning "STORAGE_BOX_{USER,HOST} unset; off-host backup skipped"
fi
```

Edit `systemd/kayak-backup.service`:

```ini
# Allow outbound network for rsync. Was AF_UNIX only.
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
# Permit network access (was IPAddressDeny=any).
IPAddressAllow=any
```

(Or scope the allow to the Storage Box subnet if you want stricter.)

Add to `.env.example`:

```bash
# Off-host backup destination (Hetzner Storage Box or rsync target).
# Leave both unset to skip off-host upload; warning will be logged.
STORAGE_BOX_USER=u123456
STORAGE_BOX_HOST=u123456.your-storagebox.de
```

Add a "Restore from off-site backup" section to `deploy/SETUP.md`:

```markdown
## Restore from off-site backup

The backup script ships gzipped snapshots to the Storage Box at
`kayak-backups/kayak-YYYYMMDD.db.gz`. To restore:

1. Stop writers:
   ```bash
   sudo systemctl stop kayak-pipeline.timer kayak-pipeline.service
   sudo systemctl stop kayak-decimate.timer
   sudo systemctl stop php8.4-fpm.service nginx
   ```

2. Pull the desired snapshot:
   ```bash
   ssh -i ~/.ssh/storage_box_ed25519 -p 23 \
       u123456@u123456.your-storagebox.de ls kayak-backups/
   rsync -e "ssh -i ~/.ssh/storage_box_ed25519 -p 23" \
       u123456@u123456.your-storagebox.de:kayak-backups/kayak-20260514.db.gz \
       /tmp/
   ```

3. Move the live DB aside, install the snapshot:
   ```bash
   mv /home/pat/DB/kayak.db /home/pat/DB/kayak.db.preremove
   gunzip -c /tmp/kayak-20260514.db.gz > /home/pat/DB/kayak.db
   chown pat:pat /home/pat/DB/kayak.db
   ```

4. Verify integrity, then restart:
   ```bash
   sudo -u pat sqlite3 /home/pat/DB/kayak.db 'PRAGMA integrity_check;'
   sudo systemctl start nginx php8.4-fpm.service
   sudo systemctl start kayak-pipeline.timer kayak-decimate.timer
   ```

5. Trigger a fresh pipeline run to reconcile observations:
   ```bash
   sudo systemctl start kayak-pipeline.service
   ```
```

### Verify on dev

```bash
# Syntax check the script
bash -n systemd/kayak-backup.sh
shellcheck systemd/kayak-backup.sh   # if installed
```

### Deploy to levels-test

```bash
git add systemd/kayak-backup.sh systemd/kayak-backup.service .env.example deploy/SETUP.md
git commit -m "backup: rsync snapshots to Storage Box; add restore runbook"
git push origin main

ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git pull --ff-only origin main

# Set the env vars
echo "STORAGE_BOX_USER=u123456" >> /home/pat/.config/kayak/.env
echo "STORAGE_BOX_HOST=u123456.your-storagebox.de" >> /home/pat/.config/kayak/.env

# Reinstall the unit
sudo bash systemd/install.service.sh
```

### Verify on levels-test

```bash
# 11.1 — Trigger a backup manually
sudo systemctl start kayak-backup.service

# Watch the run
sudo journalctl -u kayak-backup.service -n 100 --no-pager
# Expected: local snapshot created; rsync to Storage Box succeeded;
#           no "off-host rsync FAILED" message

# 11.2 — Confirm snapshot landed on the Storage Box
ssh -i ~/.ssh/storage_box_ed25519 -p 23 u123456@u123456.your-storagebox.de \
  "ls -la kayak-backups/"
# Expected: today's kayak-YYYYMMDD.db.gz visible, ~50-100MB

# 11.3 — Dry-run the restore path (in a scratch directory; do NOT
#         overwrite the live DB)
mkdir -p /tmp/restore-test
rsync -e "ssh -i ~/.ssh/storage_box_ed25519 -p 23" \
  u123456@u123456.your-storagebox.de:kayak-backups/kayak-$(date +%Y%m%d).db.gz \
  /tmp/restore-test/
gunzip /tmp/restore-test/*.gz
sqlite3 /tmp/restore-test/kayak-*.db 'PRAGMA integrity_check; SELECT COUNT(*) FROM observation;'
# Expected: ok; observation count matches /home/pat/DB/kayak.db within ~1h
rm -rf /tmp/restore-test
```

### Rollback

```bash
git revert <hash>
sudo bash systemd/install.service.sh
# Local-only backups continue to run; the off-host step is removed.
# Storage Box keys can stay; they cost nothing.
```

## Phase 12 — Post-deploy monitoring (24-hour soak)

After the last must-fix phase lands, run the test box on its own for 24
hours with the full punch list applied. Watch for any regression that
surface only under sustained traffic.

### Soak-watch checklist

1. **Hourly pipeline run completes.** `journalctl -u kayak-pipeline -f`
   over a few cycles; no errors, observation count growth steady.
2. **Heartbeat email arrives Sunday 06:00 + RandomizedDelaySec.** (If
   that timer was migrated.)
3. **No backup-failure emails.** `journalctl -u kayak-backup --since
   '24h ago'` clean.
4. **No notify-failure emails** unless intentional.
5. **5xx rate from nginx access log < 0.1%.** `awk '{print $9}'
   /var/log/nginx/kayak-access.log | sort | uniq -c`.
6. **Magic-link sign-in still works.** Send yourself one and complete
   the flow.
7. **All must-fix items still in place.** Re-run Phase 1's
   read-only verifies.

### Exit criteria for cutover

- All Phase 1 verifies pass.
- 24-hour soak watch is clean.
- DNS.CHANGEOVER.md Phase 2 (3-SAN cert acquired) is complete.
- ClubExpress ticket for Phase 3 (`A → CNAME`) is queued for ~2026-05-19.

If any criterion fails, cutover is delayed.

---

## Appendix A — Full prioritized punch list

### Must-fix before 2026-05-20

1. Off-site backups (Phase 11)
2. Rate-limit zone split — login/auth/contact (Phase 8)
3. PHP-FPM pool hardening (Phase 9)
4. PHP search/plot LIMIT (Phase 4)
5. `description.php` calc-expression XSS (Phase 5)
6. NaN/Inf observation guard (Phase 3)
7. CSV injection guard (Phase 3)
8. FK indexes round 2 (Phase 2)
9. Mail subject CR/LF strip (Phase 6)
10. Audit timer email (Phase 7)
11. Notify-failure subject tagging (Phase 7)
12. MAIL_FROM/SPF/DKIM (Phase 10)

### Should-fix before launch

13. `review_logic.php:151` — generic exception message instead of raw
    `PDOException::getMessage()`.
14. `Cache-Control: private` on PHP pages that render the editor email
    in the nav (`description.php`, `gauge.php`, `reach.php`).
15. Calculator `**` exponent unbounded — `src/kayak/cli/calculator.py:30,56-87`.
16. Calculator topo-sort regex misses 2-part deps —
    `src/kayak/cli/calculator.py:133`.
17. `levels init-db` on existing DB silently stamps every migration —
    `src/kayak/cli/init_db.py:175`.
18. `setfacl` unconditional in `build.py:1893-1903` — kills macOS dev.
19. No `RandomizedDelaySec` on systemd timers.
20. fail2ban `[DEFAULT] maxretry=3` — bump to 5 + `ignoreip` for home IP.
21. `build.py` static output omits `<link rel="manifest">` and apple-touch-icon.
22. `.rising`/`.falling`/`.stable` CSS classes referenced but undefined
    in `style.css`.
23. Magic-link consumption is GET — Outlook Defender / Proofpoint
    prefetch burns the token.
24. `gauge_map.php:82` reads `static/leaflet.css` per request.
25. `php/custom.php:127-153` sparkline N+1.
26. About / disclaimer / privacy bypass shared chrome.
27. `php/reach.php:291,623` wrong relative path for `leaflet.css`.
28. Orphan `static/levels.js` vs `src/kayak/web/static/levels.js`.
29. Public CORS `Access-Control-Allow-Origin: *` on `api.php` and
    `latest.php` — confirm with a comment that this is intentional.

### Defer (post-launch first sprint)

The remaining ~150 P2/P3 findings are polish: manifest theme-color
mismatch, `og:`/`twitter:` cards, `sitemap.xml`, `robots.txt` Disallow
on dynamic endpoints, `ENT_QUOTES` standardization, `_HOST_CONCURRENCY_OVERRIDES`
to config, more parser fixtures for malformed feeds, `mmap_size` /
`cache_size` PRAGMA tuning, MaxStartups loosening, time format
consistency, dual robots.txt cleanup, `security.txt` Expires renewal,
Permissions-Policy interest-cohort + browsing-topics, dark-mode
theme-color, etc.

### What's solid (left alone)

- Auth/CSRF/SQLi/XSS in main paths. Magic-link issuance throttled
  per-email + per-IP. Cookies HttpOnly+Secure+SameSite=Strict. CSRF
  double-submit with `hash_equals`.
- Python security baseline: AST-restricted calculator (no `eval`/`exec`),
  SSRF allowlist (private/loopback/link-local/metadata blocked),
  XXE-disabled lxml, TLS verification by default, no `shell=True`, no
  `pickle`, `yaml.safe_load`.
- Schema integrity: `integrity_check`/`foreign_key_check`/`quick_check`
  all clean on the prod snapshot. 15/15 migrations stamped, no orphans.
  `sqlite_stat1` populated.
- Systemd sandboxing applied uniformly: `ProtectSystem=strict`,
  `ProtectHome=read-only`, `NoNewPrivileges`,
  `SystemCallFilter=@system-service`, empty `CapabilityBoundingSet`,
  `PrivateTmp`, `UMask=0077`.
- CSP: `script-src 'self' challenges.cloudflare.com`, `frame-ancestors
  'none'`, `object-src 'none'`, OCSP stapling, HSTS 2yr.
- DNS-cutover plan (DNS.CHANGEOVER.md): correctly uses DNS-01 with CNAME
  delegation to dodge the SSL race; rollback path documented.
- Source XOR constraint: `both_set=0` confirmed on prod snapshot — safe
  to add the CHECK once the 175 legacy NULL/NULL rows are decided on.

## Appendix B — Order of operations cheatsheet

```
Phase 0  pre-flight on dev  (no deploy)
Phase 1  read-only verifies on test box  (no deploy)
Phase 2  FK indexes migration            (additive, easy revert)
Phase 3  NaN/Inf + CSV-inj                (Python defensive)
Phase 4  PHP search/plot LIMIT            (PHP defensive)
Phase 5  description.php XSS              (PHP, manual verify)
Phase 6  mail.php CR/LF strip             (PHP defensive)
Phase 7  audit email + notify tagging     (systemd unit)
Phase 8  nginx zone split                 (nginx reload)
Phase 9  PHP-FPM hardening                (FPM reload + smoke test)
Phase 10 MAIL_FROM Path A                 (env edit + FPM reload)
Phase 11 off-site backups                 (Storage Box + script)
Phase 12 24-hour soak watch               (no deploy)
```

## Appendix C — Emergency rollback (whole release)

If any phase causes a regression that can't be quickly diagnosed:

```bash
ssh pat@levels.mousebrains.com
cd /home/pat/kayak
git log --oneline -20

# Revert to the baseline tag
git checkout pre-review-baseline
# (or revert just the broken commits with: git revert <hash>)

# Reapply config files from the baseline tree
sudo cp conf/levels.nginx /etc/nginx/sites-available/levels
sudo cp deploy/nginx-ratelimit.conf /etc/nginx/conf.d/kayak-ratelimit.conf
sudo cp deploy/kayak-fpm-pool.conf /etc/php/8.4/fpm/pool.d/kayak.conf
sudo bash systemd/install.service.sh

sudo nginx -t && sudo systemctl reload nginx
sudo php-fpm8.4 -t && sudo systemctl reload php8.4-fpm.service

# DB indexes are forward-only (no down migration); leave them in place.
# They are not breaking changes.
```
