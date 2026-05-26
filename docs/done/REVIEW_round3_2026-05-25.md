# Kayak — Deep Project Review (Round 3)

> **Archived 2026-05-26 — historical record, not a description of current state.**
> This was `REVIEW.md` on the now-superseded `review-3` branch. Every finding was
> remediated across PRs **#34–#45** — the seven-phase plan in
> [`PLAN_round3_remediation.md`](PLAN_round3_remediation.md). Kept as the **trend
> baseline** (the grade series + the doc/ops-hygiene thesis); for the next round,
> review the code cold *first*, then reconcile against this so prior findings
> don't anchor the new sweep.

**Reviewed:** 2026-05-25 · branch `review-3` (off `main` @ `7667794`)
**Prior rounds:** R1 (2026-05-23) **B−** → R2 (2026-05-24) **B−**, archived at
`docs/done/REVIEW_round2_2026-05-24.md`. R2's findings were all remediated in PR #25.
**Method:** 6 parallel facet auditors (architecture/data, Python, PHP/security, testing/CI,
ops/deploy/hardening, docs/hygiene), each told to review cold then reconcile against R2.
**Every HIGH and every security finding below was then re-verified by hand against source** —
which corrected two agent over-claims (see Convergence note). Iterated to convergence.
**Scope:** entire tracked repo. Extra scrutiny on the surface merged since R2's remediation
(PR #25): **#28** worktree workflow + snapshot guard, **#29** PHPStan level 9 + strict-rules,
**#30** `_internal` CSP classifier, **#31** PHP coverage 9→60% + 55% ratchet, **#32** SETUP.md
prod-path fixes.

---

## Executive summary

The craft kept rising and it is real and verified: **#29 took PHPStan to level 9 + full
strict-rules and lands clean** behind a 104-entry baseline that is *only* `mixed`-typing
narrowing artifacts — no auth/injection/XSS finding is suppressed in it. **#31 lifted PHP
coverage from 9% to ~60%** with genuine behavioral tests via an in-process `FunctionalTestCase`,
and `includeUncoveredFiles="true"` keeps the floor honest. **#30's CSP classifier fix is correct
and regression-tested.** The auth/session/CSRF/SQL/CSP core re-verified sound. **Every R2 fix
held** under re-check. The systemd sandboxing suite is A-tier. This is a well-engineered project.

And the diagnosis is, for the **third round running, the same one** — only this round it shows up
in two places at once. First, the seam-discipline lag R1/R2 named recurred *on schedule*: a
session after #29 made PHPStan level 9 the gate, **CLAUDE.md still says "level 8" in two places**
and documents a narrower lint/mypy scope than CI actually runs; the CHANGELOG dropped 3 of the
last 7 PRs; the PHP-testing doc describes only half the (now-primary) test harness. Second — and
new this round because prior rounds under-audited it — **a harder look at ops/deploy surfaced
real HIGHs the craftsmanship elsewhere would never tolerate**: a `sudoers` wildcard that grants
`pat` arbitrary root file-write while its own comment swears it "cannot modify anything outside
/etc/kayak"; a default nginx vhost that references a TLS cert no install step creates (fresh
`nginx -t` fails); a second `sudoers` grant pinned to backup units that were renamed away. And a
trust hit: the schema reference doc R2 explicitly vouched for as "in lockstep with `models.py`"
is in fact **missing at least four columns**.

The thesis holds and sharpens: **the improvement loop (good features, real gates) and the debt
loop (doc/ops-hygiene lag) are running at the same rate.** Three rounds at B− is itself the
finding.

### Overall grade: **B−** (flat — R1 B−, R2 B−, R3 B−)

The level-9/coverage/worktree wins earned a bump; the recurring doc drift plus three newly-surfaced
ops/security HIGHs cancelled it.

| Axis | Grade | One-line |
|---|---|---|
| Self-consistency | **B−** | `level 8`↔`level 9`, `OUTPUT_DIR` "outside repo"↔symlink-into-repo, gradient still N-sourced (0051/0058/0059 disagree for 2 reaches), CHANGELOG drift. |
| Drift (docs/config vs reality) | **C+** | Canonical contributor doc (CLAUDE.md) misstates the PHPStan level *and* the lint/test scope; schema doc missing ≥4 columns; CHANGELOG omits #26/#28/#32. |
| New-maintainer docs | **C+** | The front door works, but the map is wrong: schema ref incomplete, PHP-test scaffold doc half the picture, two completed plans unindexed. |
| Ops / deploy correctness | **C+** | A-tier systemd sandboxing undercut by a deploy-blocking missing cert, a root-write `sudoers` wildcard, stale backup-unit grants, and two backup-restore runbook bugs. |
| Security | **B** | Auth/CSRF/SQL/CSP/session all verified sound; pulled down by the `emit-config` sudoers wildcard (HIGH) and a CSP-mitigated `javascript:`-URI passthrough (MED). |
| Test coverage of recent work | **B** | #29/#31 are the strongest seam-discipline step yet — real PHP coverage, level-9 clean. Pulled by a CI-skipped tracing path and a dev-only test failure. |

### Per-dimension grades (Δ vs. R2)

| Dimension | Grade | Δ |
|---|---|---|
| Python package core (`src/kayak/`) | **A−** | = |
| Schema & migration consistency | **B** | ▼ from A− (schema-doc drift; 0059 chain) |
| Data & config consistency | **B** | = |
| Documentation & onboarding | **C+** | ▼ from B− (CLAUDE level-8, scope, scaffold) |
| CI & testing infra | **B** | ▲ from B− (L9 + real PHP coverage) |
| PHP & frontend | **B** | ▲ from B− |
| Ops / deploy | **C+** | = (sandboxing A-tier; runbook/sudoers HIGHs) |
| Security | **B** | (new explicit axis) |
| PR discipline | **C+** | = (clean commits; CHANGELOG/doc lag every batch) |
| Tracked-artifact bloat | **B−** | = (deferred items still deferred) |

---

## Findings by theme

Severity: **CRIT** / **HIGH** / **MED** / **LOW**. Evidence is `file:line` / migration / commit.
**Every HIGH and every security item was re-verified against source this round.** No CRIT this
round (R2's CRIT — the dangerous `migrations.md` recovery doc — was fixed and holds).

### 0. Security & ops correctness (the round-3 sharp edges)

- **HIGH [security] — The `emit-config` sudoers grant's trailing `*` permits arbitrary root file-write, contradicting its own safety comment.** `deploy/sudoers.d/kayak-emit-config:31` is `pat ALL=(root) NOPASSWD: /home/pat/.venv/bin/levels emit-config*`. A trailing `*` after a command in sudoers matches *any* additional arguments, so `sudo levels emit-config --out /etc/sudoers.d/x` (or any root path) is permitted. `emit_config.py:118` does `path.parent.mkdir(parents=True, exist_ok=True)` and `:147` atomically writes to the caller-supplied `--out` with **no path restriction**. The file's own header (`:10-11`, `:25-28`) claims the grant "never strays outside the levels CLI's emit-config subcommand" and "cannot … modify systemd, or touch the database" — **false**: it can clobber any root-owned file with the config JSON (whose values `pat` partly controls via `~/.config/kayak/.env`). Precondition is code-exec as `pat` (not `www-data`, which is correctly separated), so this is a least-privilege / defense-in-depth failure that converts any `pat` compromise into root — exactly the escalation the rest of the hardening works to prevent. **Fix:** drop the `*` and pin the full argument (`emit-config --out /etc/kayak/runtime-config.json`), plus a `--dry-run` variant.
- **HIGH [deploy-blocker] — The default nginx vhost references a TLS cert no install step creates.** `deploy/nginx-default-server:23-24` sets `ssl_certificate /etc/nginx/ssl/dummy.crt` / `ssl_certificate_key …/dummy.key`; the comment says "(generated by install script)". Verified: `deploy/install-config.sh` has **no** openssl/cert step, and `deploy/SETUP.md` has **no** dummy-cert step — yet SETUP.md §6 installs this file as `sites-available/default` (the enabled bare-IP catch-all that needs a cert to 444 on `:443`). On a fresh box `nginx -t` fails → nginx won't start → whole site down, with no runbook pointing at the cause. Latent/pre-existing (prior rounds under-audited ops). **Fix:** add the `openssl req -x509 -nodes … dummy.crt dummy.key` step to `install-config.sh` (or SETUP.md §6).
- **HIGH [ops] — The `kayak-pipeline` sudoers grant covers retired backup unit names; `db_push.sh` drives the real ones.** `deploy/sudoers.d/kayak-pipeline:35-38` grants start/stop for `kayak-backup.timer` / `kayak-backup.service` — units that **do not exist** (the backup was split into `kayak-backup-{hourly,weekly,offsite}.{service,timer}`). `scripts/db_push.sh:93-95,137,155` stops/starts `kayak-backup-weekly.{timer,service}` + `kayak-backup-hourly.{timer,service}` — none covered by the NOPASSWD grant. A live-DB swap via `db_push.sh` hits a sudo password prompt (or denial in a non-interactive context) mid-flight while the pipeline is paused. **Fix:** update the grant to the split unit names.
- **MED [security] — `sanitize_source_url` passes `javascript:`/`data:` URIs through to a clickable link in the maintainer review UI.** `php/includes/source_url.php:31-34`: `parse_url('javascript:alert(1)')` yields no `host`, so the `if ($host === '') return $raw;` "relative path is always OK" branch returns the URI unmodified. It is stored in `change_request.source_url` and rendered at `php/includes/review_handler.php:269` as `<a href="' . htmlspecialchars($src) . '">` — `htmlspecialchars` does **not** neutralize a `javascript:`/`data:` scheme in `href`. Any authenticated editor (incl. `pending`) can set the hidden field; a maintainer who clicks runs script in their session. CSP (`script-src 'self'`, no `unsafe-inline`) blocks `javascript:` navigation in current browsers, so this is defense-in-depth, not live RCE — but the sanitizer is the wrong place to rely on CSP. **Fix:** reject any non-empty scheme that isn't `http`/`https`. No test covers `javascript:`/`data:` in `SourceUrlTest.php` (verified).

### 1. Drift (documentation/config vs. reality) — the recurring pattern

- **HIGH [docs/trust] — The schema reference doc is missing ≥4 columns present in `models.py`; R2 vouched it was "in lockstep."** `docs/database-schema.md` gauge table (`:25-45`) omits `river`, `display_name`, `sort_name` (`models.py:125,127,128`); source table (`:59-65`) omits `timezone` (`models.py:188`); `calc_expression` (`:97-104`) omits `provenance_slug` (`models.py:286`). These columns predate R2, so R2's "schema doc ↔ models.py ↔ live_schema.sql in lockstep (verified)" claim was **overstated** — the canonical DB map a new maintainer reads is materially incomplete. *(The agent also alleged a `latest_observation.source_id` RESTRICT→CASCADE drift; re-verified **false** — both doc `:137` and `models.py:359` say RESTRICT.)*
- **MED — CLAUDE.md misstates the PHPStan level *and* the lint/test scope, a session after #29.** `CLAUDE.md:124` (`# … (level 8)`) and `:130` ("PHPStan runs at **level 8** … `PDOStatement|false`/`string|false`-narrowing finds") are both wrong: `phpstan.neon:18` is `level: 9` and the 104-entry baseline holds `mixed`-typing (cast) finds, not the PDO-narrowing ones named. `CLAUDE.md:104,107` show `ruff check src/ tests/` and `mypy src/`, but `ci.yml:40,203` run `ruff … src/ tests/ scripts/ docs/one-offs/` and `mypy src/ scripts/import_metadata.py scripts/export_metadata.py` — a contributor following CLAUDE.md silently misses the `scripts/` gate #25 added. `ci.yml:133` further says baseline "79" (actual 104). The single most-read contributor doc misdescribes three gates.
- **MED — CHANGELOG `[Unreleased]` omits #26, #28, #32 — the omission pattern R2 fixed recurred with the next merge batch.** Absent: #26 (recap struct-log fix), #28 (worktree workflow + snapshot guard — a new operational convention), #32 (the SETUP.md prod-path fix). #29/#30/#31 are present. R2 found 6 omissions; #25 filled them; three reappeared immediately.
- **MED — CLAUDE.md's PHP test-scaffold section documents only half the (now primary) harness.** `CLAUDE.md:137-143` documents only the subprocess `IntegrationTestCase` (pcov-invisible). #31 added `FunctionalTestCase` (in-process) as the primary vehicle — it is what drove 9→60% — and CLAUDE.md never mentions it, so a new contributor defaults to the slow, uncounted pattern.
- **MED — `OUTPUT_DIR` story is self-contradictory across the canonical docs.** `CLAUDE.md:18,22` calls `/home/pat/public_html` a "regular directory … (outside the repo)" that `levels build` writes to and "never touches the repo tree"; `deploy/SETUP.md` creates it as `ln -sfn /home/pat/kayak/public_html /home/pat/public_html` — a symlink **into** the repo — and §3's env template omits `OUTPUT_DIR` entirely (only the §B quick-start sets it), so a §3-path operator's `levels build` writes into the repo tree. Two docs, three stories.
- **MED — `check-config-drift.sh` will never detect drift in the unattended-upgrades local config.** `scripts/check-config-drift.sh:72` expects `/etc/apt/apt.conf.d/50-unattended-upgrades-local` (dash); `deploy/SETUP.md:498` installs `50unattended-upgrades-local` (no dash). The names never match, so that manifest entry reports "MISSING" forever and real drift in it is masked.

### 2. Self-consistency / correctness

- **MED — The 0051/0058/0059 migration chain is not self-consistent for reaches 134 & 186 on the sequential-migrate path.** `0051:42,44` set `gradient_unreliable=1` for EF Owyhee (134) and SF Coquille (186); `0058:24` lifts `gradient_unreliable=0` only for `(117,127,155,244,251,262,299,314,405)` — **not** 134/186; `0059:25,29` then write `max_gradient`+`gradient_profile` for 134 and 186. So a pure `levels migrate` ends with those two flagged-unreliable-but-carrying-restored-data (the PHP chart suppresses the data 0059 just wrote). `reach.csv` has them at `gradient_unreliable=0`, so a fresh `init-db --no-seed`→`import_metadata` rebuild diverges from a migrate-upgrade for these rows. 0059's own header (`:4-13`) concedes it exists to patch a fresh-apply ordering hazard the chain created. The "gradient has N sources of truth" R2 carryover, now with a concrete divergence. (Deferred in R2 as feature-sized; still open.)
- **MED — `pipeline.py` treats `SystemExit` from any step as success.** `src/kayak/cli/pipeline.py:233-234` (`except SystemExit: results[step.name] = _Result.ok`). Safe today (all steps raise `RuntimeError`), but any future step that calls `sys.exit(1)` would silently pass the pipeline and the OnFailure alert chain. The footgun is documented only in `cli/check_reaches.py:285`, not where the behavior lives.
- **LOW — Carried over, unaddressed:** `source.agency` mixes parser names with agency names (`USGS`×171 vs `nwps`×5 …) so any `GROUP BY agency` miscounts; `tracing/trace.py:429-435` HUC4-disagreement tiebreak still has no test (osgeo-gated suite skips in CI); `migrate.py:106-110` `stamp_all_known()` re-queries per migration (O(N²) on init); `docs/database-schema.md:86` still lists `usgs` as a parser-name example (not a registered parser).

### 3. Testing & CI

- **MED — Three `test_config.py` subprocess tests fail on any non-venv dev environment.** `tests/test_config.py:148-243` set `env["HOME"]=fake_home` to exercise dotenv precedence; that also moves Python's user-site path, so the subprocess can't import `kayak` unless it was installed into the *venv* site-packages (CI) rather than user-site (a dev laptop). Observed: `ModuleNotFoundError: No module named 'kayak'`. CI is green (venv install), so it hides on a developer machine. **Fix:** inject `PYTHONPATH=…/src` alongside `HOME`.
- **MED — pre-commit `mypy` is pinned to `v1.14.1`; CI resolves `v1.20.1` (uv.lock) — six minor versions apart.** `.pre-commit-config.yaml` (mirrors-mypy `rev: v1.14.1`) vs `uv.lock`. #25 aligned mypy *scope* but not *version*; a type-check that 1.14 accepts and 1.20 rejects (or vice-versa) splits local vs CI.
- **MED — `httpx` is still undeclared/unlocked after two rounds.** `scripts/refresh_reach_elevations.py:27` imports `httpx`, absent from `pyproject.toml` and `uv.lock`; `ci.yml` excludes the script from mypy because of it. The script can't run in a clean clone. R2 flagged and deferred this; unchanged.
- **LOW — Gate-scope gaps:** CI `shellcheck scripts/*.sh systemd/*.sh` (`ci.yml:85`) omits `deploy/install-config.sh` (pre-commit covers it — parity gap); the `kayak.tracing.*` mypy override (`pyproject.toml:106-108`) disables `disallow_untyped_defs`/`check_untyped_defs` for the whole package, exempting the fully-typed, CI-tested `tracing/format.py`; `Makefile` `lint`/`typecheck` are narrower than CI (`make check` can be a false green); the PHP 55% floor sits ~5 pts below actual with an advisory-only "ratchet up" note and no enforcement; the L9 baseline has been static at 104 since #29 despite the config calling it "a SHRINKING debt list."

### 4. Ops / deploy / backups

- **MED — Two backup-restore runbook bugs in `docs/offsite-backup.md`.** (a) `:75` restores `kayak.db` with `chmod 664` — **world-readable** — while `docs/operations.md:147` correctly uses `660`; the DB holds editor emails, sessions, and magic links. (b) `:71-76` omits the `rm -f kayak.db-wal kayak.db-shm` step that `operations.md:142-143` has — restoring a backup without clearing the stale WAL lets SQLite replay it on top, potentially re-introducing the corruption the restore was meant to fix.
- **MED — `deploy.sh` has no branch check.** `scripts/deploy.sh:53-77` requires a clean tree and the `pat` user but never checks `git symbolic-ref HEAD`. If the live tree is on a feature branch (the exact scenario #28's snapshot-guard was written to prevent), `deploy.sh` fast-forwards it, migrates, and builds against feature code — silently promoting a branch to prod. The snapshot path guards this; the deploy path does not.
- **LOW — `SETUP.md:492-494` states the reboot/backup window backwards** ("04:00 … *before* the 03:15 weekly backup"); 04:00 is *after* 03:15 (the rationale is fine, the prose misleads). **LOW — `operations.md:698-700`'s "stop everything" one-liner prints the ACTIVATES (service) column, not UNIT (timer)** — stopping services without their timers lets the timers re-fire. **LOW — backup/decimate/heartbeat units grant `ReadWritePaths=/home/pat/.config`** (incl. `rclone.conf`) without writing there.

### 5. Repo hygiene / bloat (carried, mostly deferred)

- **LOW — Repo-root strays (cleared this session):** `virgin.txt` (VM setup log) **removed**; `montana/mt.list` — the one-off curated USGS-site input `scripts/generate_mt_migration.py` reads to regenerate migration `0036` — **archived to `docs/one-offs/` with the generator + plan-doc links repointed** (kept as provenance, not deleted); `legacy/` and `tpw.*` already gitignored (`.gitignore:5,18`). Remaining: `Gauge-metadata-cache/` is untracked but unignored, unlike its peer cache dirs.
- **LOW — Bloat deferred from R2 persists** (consciously): `huc_name.csv` 17,037 rows / 668 KB with only 403 read; `reaches.json` 2.4 MB; migration `0046` 1.7 MB duplicating the `reach.csv` gradient blob.
- **LOW — `docs/PLAN_php_testing.md` and `docs/PLAN_phpstan_level9_strict.md` are marked complete but absent from `docs/done/README.md`** (and not relocated), unlike the indexed peers.

### 6. Build / frontend / contributor onboarding / supply-chain (convergence passes 2–3)

- **MED — The service worker stores `no-store` PHP responses, so an authenticated `_internal`/editor page can be served from cache after the session is gone.** `static/sw.js:31-34` `cache.put()`s every `res.ok` response; per the SW spec that ignores HTTP `Cache-Control`, so `/_internal/index.php` and every editor endpoint (all `no-store`, `levels-common.conf:212-293`) land in `CacheStorage`. On the 3-second network-timeout race (`sw.js:23-27`) the stale authenticated HTML (source-freshness counts, CSP-violation detail, DB size) is served unchallenged even after the session is revoked. **Fix:** skip `cache.put()` when the response carries `Cache-Control: no-store`.
- **MED — CI runs every GitHub Action on a floating major tag; none are SHA-pinned.** `.github/workflows/ci.yml` uses `actions/checkout@v6`, `setup-python@v6`, `setup-node@v6`, plus **third-party** `shivammathur/setup-php@v2`, `biomejs/setup-biome@v2`, `gitleaks/gitleaks-action@v2`, `astral-sh/setup-uv@v8.1.0`. A moved tag or compromised third-party action repo runs in CI with the workflow token — inconsistent with the sshd/nftables/fail2ban/gitleaks hardening bar elsewhere. Dependabot (if its `github-actions` ecosystem is on) mitigates *version drift* but not *tag mutation*. **Fix:** SHA-pin at least the third-party actions.
- **MED — `CONTRIBUTING.md`, the contributor front door, soft-fails a new dev twice.** `:13-15` quick-start is `levels init-db` → `make check` with no `--no-seed` and no `scripts/import_metadata.py`, so sources orphan and `levels pipeline` soft-fails to an empty site (CLAUDE.md has the correct sequence; CONTRIBUTING doesn't). And `make check` needs a global `biome` that `package.json` never declares (CI installs it via `biomejs/setup-biome`), so the doc's "required" gate dies at `biome: command not found` on a clean clone.
- **LOW — `/reach.php` loads the superseded `reach-map.js` (no OSMB overlays) while `description.php`/`gauge.php` load the grown `feature-map.js`.** `reach_detail.php:642` vs `description_detail.php:88` / `gauge_detail.php:850`; `data.php:170` still links `/reach.php`, so a live page lacks the OSMB hazard/access layers. R2's "reach-map.js ≈ feature-map.js" dup LOW has become a feature gap.
- **LOW — The `_internal` dashboard's doc + `deploy.py` + vhost comments say it's served on `levels.mousebrains.com`; post-DNS-cutover it's on `levels.wkcc.org`.** `php/_internal/index.php:10`, `src/kayak/web/build/deploy.py:238`, `conf/sites/levels-wkcc-org:48-49`. The `require_maintainer()` gate is correct (verified) — orientation hazard only.
- **LOW — biome's *formatter* is disabled (`biome.json:13`)**, so CI lints JS but enforces no style (R2 carryover, confirmed); `CONTRIBUTING.md`'s `make check # all three` also mislabels a 5-linter gate.

---

## What's genuinely good (verified this round)

- **#29 PHPStan level 9 + full strict-rules lands clean.** `phpstan.neon:18` is `level: 9`, strict-rules included with no toggles; the 104-entry baseline is *entirely* `mixed`-typing (PDO `fetch()` row) narrowing — **no auth/injection/XSS finding is parked there** (verified by reading the baseline).
- **#31 is the strongest seam-discipline step since #25.** PHP coverage 9→60% via an in-process `FunctionalTestCase`; spot-checked tests drive real branches (daily-cap, honeypot, coordinate-reject, race-safe `rowCount()===0`, expired/revoked/banned), not assert-true theater. `includeUncoveredFiles="true"` makes the floor a genuine regression gate.
- **#30 CSP classifier fix is correct + regression-tested** — injected inline at the document URL now classifies as "Injected (proxy/extension)," with the exact Google-proxy production pattern as a test case.
- **The security core re-verified sound:** single-use transactional magic-link consume, GET-only peek, hash-at-rest, 30-min TTL, 5/email + 20/IP throttle, `safe_next_url` open-redirect guard (tested); `hash_equals` CSRF double-submit on all 10 POST handlers with rotation on escalation; all dynamic SQL field-names come from a server-side allowlist, all values parameterized; `script-src 'self'` (no `unsafe-inline`); zero `mb_*` calls.
- **systemd sandboxing is A-tier** — `ProtectSystem=strict`, scoped `ReadWritePaths`, empty `CapabilityBoundingSet`, `NoNewPrivileges`, `ProtectKernel*`, `ProcSubset=pid`, `SystemCallFilter=@system-service` across all 15 units with per-unit justification.
- **#28 worktree safety is real** — `snapshot_metadata.sh` correctly refuses non-`main`, pre-existing out-of-scope staged files, and diverged-from-origin; `new-worktree.sh` is shellcheck-clean.
- **Every R2 remediation held** under re-verification: `migrations.md` rebuild runbook, `deploy.sh` geom-apply gate, `import_metadata` PK-upsert (geom-preserving, accurate rowcount), `_localize`→`BaseParser`, `check_reaches` int-return, `scripts/` in ruff/mypy gates, `.gitattributes`, `M_TO_FT` canonical home.
- **Python package core stays A−** — parser registry, pipeline DAG, tracing math, the typed config layer; and **#32's SETUP.md path fixes are coherent and verified** (venv `~/.venv`, config `~/.config/kayak/.env`, www-data ACLs, `adm` group all consistent with the systemd units).
- **The calc-expression evaluator is a real AST sandbox** (`cli/calculator.py:50-102`): node-whitelist (no attribute/dunder access, no `eval`/`exec`/`__import__`), exponent capped at ±32 (no `9**9**9` DoS), 512-char cap; `__import__('os').system(...)` raises `ValueError` (tested), cycle detection raises on no-progress, and `calc_expression` rows are migration/admin-only (no editor write path).
- **`decimate`, the latest-observation cache rebuild, the OSMB fetch, and the rDNS/GeoIP resolvers verified clean** (single-transaction, bounded, escaped) under the convergence sweep; the Python static build HTML-escapes all user-sourced names/descriptions — no XSS in the generated pages.

---

## Prioritized remediation

**Tier 1 — security / deploy-blocking (do first):**
1. Pin the `emit-config` sudoers grant to its full argument (drop the trailing `*`) — closes the arbitrary-root-write path and makes the file's safety comment true again. *(HIGH)*
2. Add the dummy-cert generation step (`openssl req -x509 -nodes …`) to `install-config.sh` / SETUP.md §6 so a fresh `nginx -t` passes. *(HIGH)*
3. Update the `kayak-pipeline` sudoers grant to the split `kayak-backup-{hourly,weekly}.{timer,service}` names so `db_push.sh` works password-less. *(HIGH)*
4. Reject non-`http(s)` schemes in `sanitize_source_url` (+ a `javascript:`/`data:` test). *(MED, security)*

**Tier 2 — drift / trust (the recurring pattern — make these part of *merge*):**
5. Fix CLAUDE.md: PHPStan "level 9", lint/mypy/test scope to match CI, add `FunctionalTestCase` to the PHP-test scaffold section; fix `ci.yml:133` baseline count.
6. Backfill the schema doc with `river`/`display_name`/`sort_name`/`timezone`/`provenance_slug` (and add a CI check that diffs the doc against `models.py`).
7. Add #26/#28/#32 to CHANGELOG `[Unreleased]`.
8. Reconcile the `OUTPUT_DIR` story across CLAUDE.md ↔ SETUP.md (and add it to the §3 env template).

**Tier 3 — correctness / runbook / hygiene:**
9. Fix the offsite-restore runbook (`chmod 660`, add WAL/SHM removal).
10. Add a branch guard to `deploy.sh` (mirror `snapshot_metadata.sh`).
11. Make 0051/0058/0059 self-consistent for reaches 134/186 (decide a single gradient source of truth — the deferred feature-sized item).
12. `PYTHONPATH` fix for the `test_config.py` subprocess tests; declare `httpx` (or port to `aiohttp`); align pre-commit mypy version to CI; fix the `check-config-drift.sh` apt filename.
13. `.gitignore` `Gauge-metadata-cache/`; index (and relocate to `docs/done/`) the two completed plans.

---

## Convergence note

R3 method: 6 cold facet auditors, then a by-hand re-verification of every HIGH and every security
finding against source. That verification pass **corrected two agent over-claims** — the alleged
`latest_observation` RESTRICT→CASCADE drift (doc and model both say RESTRICT) and a baseline count
of 634 (actual 104) — and **confirmed the rest**: the `emit-config` wildcard, the missing dummy
cert, the stale backup-unit sudoers grant, the 4-column schema-doc gap, the `javascript:`-URI
passthrough, and the 0058/0059 reach overlap were each read in source. **Pass 2** (2 agents over the build/frontend/`_internal` surface and the calc-evaluator / decimate / cache / GeoIP / contributor-doc surface) produced §6 — one MED (the service-worker `no-store` caching) plus contributor-doc and frontend items — and **verified clean** the calc-expression AST sandbox, the decimation and cache-rebuild transactions, the rDNS/GeoIP path, and the static-build HTML escaping. **Pass 3** (a targeted self-check) added one MED (unpinned CI actions) and otherwise came up empty (zero `TODO`/`FIXME` in `src/`+`php/`, no reachable `eval`/`exec` in PHP, e2e smoke present). The per-pass finding rate decayed 6-facet → ~5 → 1 — the convergence signal — so the review stops here.

**Thesis (3rd round, unchanged):** the engineering is good and getting better — level-9 typing,
real PHP coverage, worktree safety, all verified — but the doc/ops-hygiene seam lags every merge
batch by one iteration, and a first hard look at the deploy layer found HIGHs the code layer would
never ship. Until "update the docs + tighten the grant + wire the runbook" is part of *merge*
rather than the next review, the grade stays pinned at B− no matter how good the features are.
