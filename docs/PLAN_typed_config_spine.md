# PLAN: T3.3 — typed config spine

> **Drafted:** 2026-05-14 against `main` at `eaa51c8`. Source plans:
> `docs/PLAN_pre_release_followup.md` § T3.3 (architecture audit
> ARCH-H7), `docs/PLAN_outstanding_followups.md` § Phase 4 (the
> sequencing).
>
> **Iter log:**
> - iter 1 (2026-05-14): 14 findings.
>   (A) `pydantic` is not yet in `pyproject.toml`; the plan must
>       declare it explicitly, not assume it's transitive.
>   (B) Pydantic's `EmailStr` requires the `email-validator` package;
>       pydantic-settings does not have an `[email]` extra, but
>       `pydantic[email]` does. Corrected Phase 0.1 to add
>       `pydantic[email]>=2,<3` *and* `pydantic-settings>=2,<3`.
>   (C) `maintainer_emails()` returns `list<string>` (comma-split env
>       OR `editor.status='maintainer'` rows OR a hardcoded fallback).
>       The pydantic field can't be a plain `EmailStr` — must be
>       `list[EmailStr]` with a comma-string parser, AND the DB-derived
>       fallback case has to be preserved (it's NOT config drift, it's
>       a deliberate fallback for "no env set → take whoever's listed
>       in `editor` table"). Decision: `MAINTAINER_EMAIL` stays as the
>       env override; pydantic field is `maintainer_emails: list[EmailStr]
>       = []`; PHP keeps the DB-rows fallback and only consults the
>       JSON when the JSON value is non-empty. The "fallback" the audit
>       cites is the *hardcoded literal* (`"pat.kayak@gmail.com"`),
>       which IS removed — the DB-rows path stays.
>   (D) Systemd `EnvironmentFile=/home/pat/.config/kayak/.env` + shell
>       `${HC_*}` in `ExecStartPost=` is the path the healthchecks
>       heartbeats actually traverse. Those URLs are consumed by
>       systemd, **not** by Python or PHP code. Pulling them into the
>       pydantic schema for validation is still useful (catches a
>       missing URL at deploy time) but they don't need to land in
>       runtime-config.json — systemd reads them direct from
>       `~/.config/kayak/.env` via `${VAR}` interpolation. Phase 0 keeps
>       them in `KayakConfig`; emit-config emits them; PHP doesn't read
>       them. Documented the asymmetry.
>   (E) `extra="forbid"` risk note was wrong. pydantic-settings only
>       consults env vars matching declared field names; unrelated env
>       vars (`PATH`, `HOME`) never enter the model. `extra="forbid"`
>       affects explicit `KayakConfig(...)` kwargs, not env reads.
>       Risk reframed: a typo in a declared name (`MAINTAINER_EMIAL`)
>       silently produces a default value because no field matches.
>       Mitigation: a `levels validate-config --known-env` mode that
>       compares the OS env's `KAYAK_*` / `MAINTAINER_*` / `SITE_*` /
>       `HC_*` / `TURNSTILE_*` / `FETCH_*` / `EDITOR_*` / `MAIL_*`
>       prefixed entries against the declared field set and warns on
>       unknown.
>   (F) "Single deploy per phase" was wrong — phases 1, 2, 3, 4 each
>       require a deploy. Replaced with a per-phase deploy table.
>   (G) `os.rename` atomicity requires same-filesystem source/dest;
>       the temp file must live in `/etc/kayak/` not `/tmp`. Spelled out.
>   (H) `levels emit-config` requires write to `/etc/kayak/`, which
>       requires root. `scripts/deploy.sh` runs as user `pat`. Three
>       options: (1) `sudo -n levels emit-config` (needs a sudoers
>       entry); (2) write to a user-writable staging path then a
>       `sudo install` step the operator runs; (3) `setcap CAP_FOWNER`
>       on the levels binary. Decision: option 1 with a NOPASSWD
>       sudoers entry pinned to that exact command. Documented + the
>       sudoers file lands in `deploy/sudoers.d/kayak-emit-config`
>       per [feedback_systemd_in_tree_copy].
>   (I) `SQLITE_PATH` survives as a fastcgi_param in v0 by special
>       case. No reason to keep it special — move it into the JSON
>       like everything else. Phase 2.5 strips ALL fastcgi_params
>       except `SCRIPT_FILENAME` + the standard `include
>       fastcgi_params`.
>   (J) "Once-per-request" deduplication clarified: the static-class
>       cache in `Config` is one-init-per-PHP-request lifecycle (each
>       FPM worker zeroes the static between requests). The fallback
>       INFO line fires at most once per request, which is what we
>       want; no cross-request dedup is needed.
>   (K) `systemctl reload php-fpm` not needed for JSON changes —
>       the once-per-request load picks up new content automatically.
>       Reload is only required when `/etc/kayak/secrets.env` changes
>       (env propagation through the FPM master). Phase 1.5 NOTICE
>       reworded.
>   (L) `levels show-config` was referenced in Phase 4.3 PHP equivalent
>       but never declared as a subcommand. Added to Phase 1 as a
>       sibling of `emit-config`.
>   (M) `KayakConfig` module-level constants (`DATABASE_URL = str(
>       config.database_url)` etc.) are computed at import time. Tests
>       that `monkeypatch.setenv("DATABASE_URL", ...)` AFTER import
>       won't see the new value through `kayak.config.DATABASE_URL`.
>       Today's tests already work this way because the same pattern
>       holds with `os.environ.get(...)`. Phase 0.2 preserves the
>       semantics — no behavior change. Added an explicit test that
>       verifies env late-binding gives the late-bound value when
>       callers re-instantiate `KayakConfig()` (the canonical pattern
>       going forward), and that module-level constants are
>       deliberately *not* refreshed (so the call-sites that already
>       use `KayakConfig()` get fresh values, and the ones that
>       imported `DATABASE_URL` got the boot-time value).
>   (N) `runtime-config.json` is a generated artifact. The
>       config-drift detector (T1.2, weekly) should explicitly
>       **exclude** it from the diff scope — comparing a generated
>       file to a static template would always flag drift. Documented;
>       added the exclusion to `scripts/check-config-drift.sh` as part
>       of Phase 1.
> - iter 2 (2026-05-14): 11 findings.
>   (O) Phase 2.4 listed typed wrappers `str/int/bool/url` but Phase
>       2.2 calls `Config::list('maintainer_emails')`. Added `list()`
>       (returns `list<string>`) to the wrapper set.
>   (P) `Config` hardcodes `/etc/kayak/runtime-config.json`; PHPUnit
>       fixtures need a path override. Added `Config::for_test(string
>       $path): Config` static factory (returns a new instance bound
>       to a temp file). Production callers use the implicit
>       singleton; tests get isolation.
>   (Q) Phase 2.5 nginx fastcgi_param removal: even if a future host
>       has `set $editor_feature on;` in `/etc/nginx/` outside the
>       repo, dropping the `fastcgi_param` line is still safe — PHP
>       reads the value through `getenv()` from the FPM-pool env
>       channel (`env[X]=$X` in `deploy/kayak-fpm-pool.conf`), which
>       is fed by `/etc/kayak/secrets.env`. Two channels collapse to
>       one (the FPM-pool env channel survives until Phase 4 closes
>       it for keys the JSON carries). Documented in 2.5 prose.
>   (R) `$_SERVER['EDITOR_FEATURE']` vs `getenv('EDITOR_FEATURE')`:
>       `getenv` reads the process env (FPM pool); `$_SERVER` for
>       CGI mode also includes fastcgi_params. Audit php/ for
>       `$_SERVER['XXX']` reads of any key we're migrating. If found,
>       migrate to `Config::str()` before Phase 2.5's fastcgi_param
>       deletion. Added as a Phase 2 prerequisite step (2.0).
>   (S) "PHP error_log → syslog" was sloppy. The real path: PHP-FPM
>       worker `error_log` calls go to the FPM master's configured
>       error log (Debian default `/var/log/php-fpm.log`), which the
>       php-fpm.service unit also captures into journald. The
>       greppable command is `journalctl -u php-fpm | grep
>       '[CONFIG-FALLBACK]'`. Reworded throughout.
>   (T) PHP's `error_log()` has no severity field. Use a distinct
>       bracketed tag (`[CONFIG-FALLBACK]`) so the operator's grep
>       has a clean handle. Documented.
>   (U) `validate-config` order in deploy.sh: must run AFTER
>       `pip install -e .` so the latest model is loaded; the
>       conditional pip-install step thus moves before validate.
>       Spelled out explicitly:
>       `git pull → (pip install if changed) → validate-config →
>        migrate → emit-config → build → orphan-check`.
>   (V) Exit-code convention for `validate-config`: 0 OK, 1 validation
>       error (field invalid), 2 unable-to-run (missing env file,
>       unreadable). Standard pattern (mirrors `scripts/health-
>       check.sh`).
>   (W) `runtime-config.json` mode 0640 root:www-data means user `pat`
>       can't `cat` it directly. Workaround: `sudo cat` or `levels
>       show-config`. Documented in Phase 1 + § Verification.
>   (X) Phase 2.5 nginx reload step elevated from a tucked-in sub-
>       bullet to its own line, since it's a deploy-required step.
>   (Y) Phase 4 PHP-FPM-pool cleanup: prune `env[X]=$X` re-exports
>       from `deploy/kayak-fpm-pool.conf` for keys the JSON now
>       carries (`MAIL_FROM`, `SITE_URL`, `EDITOR_FEATURE`,
>       `TURNSTILE_SITE_KEY`). `TURNSTILE_SECRET` STAYS in the FPM
>       env channel — it's the only secret today, and even with JSON
>       carrying secrets, keeping it in the env channel is a
>       defense-in-depth belt (the JSON is mode 0640 to www-data
>       group only, but env vars are per-process). Body of Phase 4
>       updated.
>   (Z) PHPUnit's spawn of `levels emit-config` needs a discoverable
>       binary path. Add a `KAYAK_LEVELS_BIN` env var defaulting to
>       `/home/pat/.venv/bin/levels` for local; CI exports the
>       runner-specific path. Documented in Phase 2.3.
>
> - iter 3 (2026-05-14): 10 findings.
>   (CC) §Why table row for systemd refers to `Environment=`; actual
>        files use `EnvironmentFile=`. Corrected.
>   (DD) Goal #5 listed `getenv()/$_SERVER` as the dual-read fallback,
>        but the Phase 2.0 audit moves $_SERVER reads to `Config::*()`
>        before Phase 2.5 deletes the fastcgi_params. The fallback is
>        getenv-only. Reworded.
>   (EE) Decisions #1 hand-waved "transitive via fastapi if it ever
>        lands." The project does NOT use fastapi. Dropped the
>        misleading parenthetical.
>   (FF) Constraint about runtime-config.json being "mirrored by an
>        in-repo install target" was inverted: it's a generated
>        artifact and the drift detector EXCLUDES it (iter 1, finding
>        N). Reworded the constraint to point at the drift-exclusion.
>   (GG) Phase 2 numbering had a `0.` prefix on `2.0` inside an
>        ordered list — renumbered cleanly so the section reads
>        `2.0 → 2.1 → 2.2 → 2.3 → 2.4 → 2.5`.
>   (HH) Effort tally needed bump: Phase 2 gains 2.0 ($_SERVER audit
>        + migration step), Phase 4 gains 4.5 (FPM-pool cleanup).
>        Updated 8h → 9h (Phase 2), 2h → 3h (Phase 4); new total
>        **22h ≈ 2.75d**.
>   (II) Fallback log severity inconsistent (INFO in some places, WARN
>        in others). Standardized on **WARN** — operationally aligned
>        with healthchecks "no ping" alerts, easy to grep at fixed
>        severity.
>   (JJ) End-state checklist gains a line noting that
>        `TURNSTILE_SECRET` is the **only** remaining
>        `env[X]=$X` entry in `kayak-fpm-pool.conf` after Phase 4.5.
>   (KK) `validate-config --known-env` prefix-allowlist could
>        false-positive on legit env vars (e.g. `OUTPUT_FORMAT`).
>        Switched to an explicit name allowlist derived from the
>        pydantic model's `Field.alias`/name set. A typo
>        (`MAINTANER_EMAIL`) doesn't match a declared field → WARN;
>        unrelated env vars don't match either prefix or name →
>        silent.
>   (LL) Dependency floors: project targets Python 3.13.
>        `pydantic-settings` 3.13 support landed in 2.4; pydantic
>        in 2.7. Tightened the deps to
>        `pydantic[email]>=2.7,<3`, `pydantic-settings>=2.4,<3`.
>
> - iter 4 (2026-05-14): 4 findings.
>   (MM) Phase-summary deploy table still said "INFO" for the
>        fallback log line; iter 2 (II) standardized on WARN.
>        Corrected.
>   (NN) Phase 0.1 dep spec missed the 2.7/2.4 floors from iter 3
>        (LL). Updated.
>   (PP) `hc_recap` URL listed in Phase 0.2 model fields, but
>        `systemd/kayak-recap.service` (added 2026-05-14, commit
>        7c22068) doesn't yet have `ExecStartPost=-curl ${HC_RECAP}`.
>        Phase 0 implementation step adds the curl ping at the same
>        time it adds `hc_recap` to the model.
>   (QQ) `output_dir` validator was "validate parent exists;
>        auto-create if not." Auto-creating from a validator is a
>        side effect. Softened to "validate existence;
>        `levels init-output-dir` is the explicit create step (added
>        as a sibling CLI in Phase 1)."
>
> Convergence: 14 → 11 → 10 → 4 findings.  Stopping; remaining items
> are aesthetic.
>
> Dates absolute. References `file:line` against current `main`.

## Why

Configuration today is fragmented across at least eight independent
read sites that agree only by convention:

| Where | Read by | What it carries |
|---|---|---|
| `~/.config/kayak/.env` | `src/kayak/config.py` via `python-dotenv` | `DATABASE_URL`, `OUTPUT_DIR`, `MAINTAINER_EMAIL`, `SITE_URL`, `FETCH_TIMEOUT`, `FETCH_USER_AGENT`, `FETCH_BUDGET`, `NTFY_TOPIC`, `HC_*` heartbeat URLs, etc. |
| `/etc/kayak/secrets.env` | PHP-FPM pool via systemd drop-in → `env[X]=$X` in `deploy/kayak-fpm-pool.conf` → `getenv()` | `TURNSTILE_SECRET`, `EDITOR_FEATURE`, `TURNSTILE_SITE_KEY`, `MAIL_FROM`, `SITE_URL`, etc. (mode 0600 root:www-data) |
| nginx `fastcgi_param` in `conf/snippets/levels-common.conf:148-302` | PHP via `$_SERVER` | `SQLITE_PATH`, plus dead-code-by-design re-passes of `EDITOR_FEATURE`, `TURNSTILE_SITE_KEY`, `MAIL_FROM`, `SITE_URL` whose `$editor_feature` etc. are never `set` in the repo — they resolve to empty string in nginx and PHP reads the FPM-pool-env copy via `getenv()` instead |
| systemd unit `EnvironmentFile=/home/pat/.config/kayak/.env` | individual `kayak-*.service` files | `HC_*` heartbeat URLs (`ExecStartPost=-curl ${HC_X}`), `NTFY_TOPIC` (notifier), `OUTPUT_DIR` overrides — all read by systemd via `${VAR}` shell interpolation, NOT by Python or PHP |
| `data/sources.yaml` | `src/kayak/config_data.py` via `@lru_cache` | Source URLs, station IDs, per-station timezones |
| `data/builder.yaml` | builder | Per-state HTML build config |
| `fetch_url` / `calc_expression` tables | both Python (CLI) and PHP (admin UI) | Runtime data, not config — out of scope |
| Hardcoded fallbacks | `config.py:43` (`"pat.kayak@gmail.com"`), `auth.php:31` (`maintainer_emails()` ditto) | Coincidence-aligned defaults that disagree silently if one is edited |

Resulting concrete failure modes the audit (ARCH-H7) cites:

- **Coincidence drift.** Python's `MAINTAINER_EMAIL` and PHP's
  `maintainer_emails()` both default to `"pat.kayak@gmail.com"`. Change
  one fallback without the other and the two layers serve different
  addresses with no failure signal — the production-discipline
  Tier 1.4 notifier email pipeline depends on these agreeing.
- **Cargo-cult fastcgi_param.** `$editor_feature` / `$turnstile_site_key`
  / `$mail_from` / `$site_url` are referenced in five `fastcgi_param`
  lines across `levels-common.conf` but never `set`. The
  config-drift detector won't catch this (it diffs files, not nginx
  semantics). A new developer reading the snippet will be misled about
  where the value actually comes from.
- **No source-of-truth for "what's the current config."** An incident
  responder has to compose the answer from grep across `.env`,
  `secrets.env`, systemd units, nginx fastcgi_param, and any
  in-flight overrides. There is no `levels show-config` or analogous
  PHP page that prints "this is what's resolved right now."
- **Type coercion duplicated.** `int(os.environ.get(..., "300"))` in
  Python and `(int)(getenv(...) ?: 5)` in PHP. Type errors land late
  (first real call site) instead of at config-load time.
- **No validation.** A blank `SITE_URL`, an unparseable `DATABASE_URL`,
  a `FETCH_TIMEOUT=abc` — all silently propagate to the call site that
  trips over them.

## Goal

A single typed, validated configuration spine such that:

1. **Python defines the schema.** `src/kayak/config.py` becomes a
   `pydantic-settings` model with explicit types, ranges, and defaults
   for every config key (env-derived only — DB-table config stays in
   the DB).
2. **One canonical resolution step.** `KayakConfig()` resolves env →
   typed config; CI fails if validation fails. No fallbacks scattered
   across call sites.
3. **PHP reads the resolved config from a JSON snapshot.** `levels
   emit-config` writes `/etc/kayak/runtime-config.json` (mode
   0640 root:www-data); `php/includes/config.php` loads it once per
   request, hits an in-process cache, and exposes typed getters.
4. **Both layers agree by construction.** The same JSON is the source
   of truth; "Python's `MAINTAINER_EMAIL` vs PHP's
   `maintainer_emails()`" can no longer drift.
5. **Backwards-compatible dual-read for one release.** PHP reads the
   JSON first; falls back to `getenv()` only — `$_SERVER` reads of
   migrated keys get rewritten to `Config::*()` before the
   fastcgi_param deletion (Phase 2.0 audit). The dual-read window
   logs a WARN every time the fallback fires; the operator confirms
   a 14d zero-fallback streak before Phase 4 deletes the
   `getenv()` arm.

Out of scope for this plan: DB-table config (`fetch_url` etc.),
hot-reload (config changes still need a deploy + FPM-pool reload),
KAYAK_HOME indirection (T3.4, separate plan).

## Constraints

- **Per [feedback_no_sudo]:** all `/etc/` edits land as diffs + a
  documented `sudo install` step the user runs; no shell sudo invoked.
- **Per [feedback_systemd_in_tree_copy]:** new in-repo
  `deploy/sudoers.d/kayak-emit-config` mirrors the `/etc/sudoers.d/`
  install target so the drift detector can compare. The generated
  `runtime-config.json` is the opposite case — it's an artifact,
  not a tracked file, so `scripts/check-config-drift.sh` explicitly
  ignores it (Phase 1.4).
- **Per [feedback_deploy_confirm]:** the dual-read shim ships before
  the PHP cutover; a separate confirmed deploy step removes the
  shim once the operator OKs.
- **Per [feedback_csp_no_inline]:** PHP code reading the JSON must
  not introduce any inline `<script>` or `<style>` (none planned, but
  the threat-model section calls this out for completeness).
- **No PHPStan baseline regressions.** `php/includes/config.php`
  enters PHPStan at level 8 from day one; no shim entries in
  `phpstan-baseline.neon`.
- **No `mbstring` regressions.** Live PHP-FPM lacks mbstring; all
  string ops in the new PHP code use `strlen`/`substr`/`strtolower`.
- **`KAYAK_HOME` is hardcoded for now.** T3.4 hasn't shipped; the
  JSON path is `/etc/kayak/runtime-config.json` and the repo path is
  derived from `Path(__file__).resolve().parents[2]` in
  `src/kayak/config.py` exactly as today. When T3.4 lands, swap to
  `$KAYAK_HOME/etc/runtime-config.json` (or whatever convention
  T3.4 picks) — call out as a follow-up, don't try to land both at
  once.

## Decisions baked in

- **`pydantic[email]>=2.7,<3` + `pydantic-settings>=2.4,<3`.** Net
  new explicit deps (no transitive path today). Floors reflect Python
  3.13 support: `pydantic-settings` 2.4 added 3.13 wheels;
  `pydantic` did in 2.7. Reasons over `attrs` + manual validation:
  best-of-class env coercion, JSON-schema export for the PHP side's
  schema-aware fallback, mature `.env` + ENV var resolution, native
  `Annotated[..., Field(...)]` validation.
- **JSON, not TOML / YAML, for the runtime artifact.** PHP has
  native `json_decode`; `parse_ini_file` flattens nested config;
  YAML requires a PHP extension. JSON also round-trips
  pydantic's `.model_dump()` cleanly. Reject TOML for the same reason.
- **One JSON file at `/etc/kayak/runtime-config.json`** (not split
  by section). The whole config is small (~30 keys); splitting buys
  nothing and complicates atomic writes.
- **Mode 0640 root:www-data.** Mirrors `/etc/kayak/secrets.env`'s
  scheme: PHP-FPM reads via www-data group, ordinary users don't see
  secrets, the file is writable only by the deploy script (running
  as root via `sudo install`).
- **`emit-config` is idempotent and atomic.** Writes to
  `<path>.tmp`, then `rename(2)` — never partial. Re-running with
  no change is a no-op (file content compares equal).
- **No env-var-name compatibility shim.** The Python env vars keep
  their current names (`MAINTAINER_EMAIL`, `SITE_URL`, etc.); the
  pydantic model field names are the same lowercased; the JSON keys
  are camelCase only at the JSON boundary if at all (probably stay
  snake_case to match Python). Avoids a rename pass on top of a
  rewiring.
- **Dual-read fallback log line is one INFO per request batch, not
  one per call.** PHP currently re-reads the same getenv() values
  many times per request; deduplicate the warning so a missing JSON
  doesn't flood the log.

## Target shape after this plan executes

A request reaching PHP:

```
nginx → PHP-FPM → php/includes/config.php (once-per-request load)
                    └── reads /etc/kayak/runtime-config.json
                        ├── present → typed getters from JSON
                        └── absent  → INFO log, fall back to getenv()
```

A `levels` CLI invocation:

```
levels <cmd> → src/kayak/config.py:KayakConfig()
                ├── reads ~/.config/kayak/.env (existing behavior)
                ├── validates types + ranges
                └── exposes typed attributes on a module-level instance
```

A deploy:

```
scripts/deploy.sh
  ├── git pull --ff-only
  ├── pip install -e .  (only if pyproject.toml changed)
  ├── levels migrate
  ├── levels emit-config → /etc/kayak/runtime-config.json
  │     (writes atomically; reload PHP-FPM only if JSON content changed)
  └── levels build
```

## Migration phases

Five phases. **Review gate between each.** Each phase ends with a
deploy; the dual-read shim makes Python + PHP land independently.

| Phase | What lands | Deploy needed | Operator action |
|---|---|---|---|
| 0 | pydantic-settings dep + schema scaffold | yes | none (Python change only) |
| 1 | `emit-config` / `show-config` CLIs + deploy.sh integration | yes | install `deploy/sudoers.d/kayak-emit-config` once; subsequent deploys run emit-config automatically |
| 2 | `php/includes/config.php` + per-file PHP migration | yes | first deploy after this phase: confirm `journalctl -u php-fpm \| grep '\[CONFIG-FALLBACK\]'` returns nothing; reload nginx after the `levels-common.conf` cleanup |
| 3 | `validate-config` subcommand + CI gate | yes | none (CI-only) |
| 4 | Drop the PHP fallback; require JSON; cleanup | yes | confirm 14d no-fallback window before the deploy that lands this phase |

### Phase 0 — `pydantic-settings` introduction (no behavior change)

**Goal:** add the schema without removing any existing read path.

1. **0.1 — Dependencies.** Add to `pyproject.toml`
   `[project.dependencies]`:
   - `pydantic[email]>=2.7,<3` — pulls in `email-validator` for
     `EmailStr`; 2.7 is the first 3.13-supporting release
   - `pydantic-settings>=2.4,<3` — env-driven `BaseSettings`; 2.4
     ships 3.13 wheels

   Run `uv lock` (per [feedback_uv_lock_sync]). CI green.
2. **0.2 — Schema scaffold.** Rewrite `src/kayak/config.py` to define
   `class KayakConfig(BaseSettings)` with the existing fields:
   - `database_url: str` (validate it parses as a URL — `AnyUrl` is
     too restrictive for `sqlite:///` paths; a string is fine here)
   - `output_dir: Path` (validator checks existence and writability;
     does not create. The explicit create step is
     `levels init-output-dir`, added as a sibling CLI in Phase 1.)
   - `maintainer_emails: list[EmailStr] = []` — env var
     `MAINTAINER_EMAIL` parsed as comma-separated; an empty list
     means "fall back to the editor-status='maintainer' rows" (the
     PHP fallback path, preserved by Phase 2)
   - `maintainer_name: str`
   - `site_url: AnyHttpUrl` (`HttpUrl` rejects `localhost`; dev
     boxes need it)
   - `fetch_timeout: int = Field(gt=0, le=600, default=300)`
   - `fetch_budget: int = Field(gt=0, le=600, default=240)`
   - `fetch_user_agent: str = "kayak/1.0"`
   - `ntfy_topic: str | None = None`
   - `mail_from: EmailStr | None = None`
   - `mail_dump_dir: Path | None = None`
   - `editor_feature: bool = False` (mirrors PHP today)
   - `turnstile_site_key: str | None = None`
   - `turnstile_secret: SecretStr | None = None`
   - `editor_session_ttl_days: int = 7`
   - **Healthchecks URLs** (`hc_pipeline`, `hc_backup_hourly`,
     `hc_healthcheck`, `hc_decimate`, `hc_editor_retention`,
     `hc_backup_weekly`, `hc_backup_offsite`, `hc_audit_gauges`,
     `hc_heartbeat`, `hc_cert_expiry`, `hc_cert_renewal_test`,
     `hc_config_drift`, `hc_metadata_snapshot`, `hc_recap`):
     one optional `HttpUrl | None = None` field per existing
     systemd-consumed heartbeat. These URLs are read **by systemd**
     via `EnvironmentFile=/home/pat/.config/kayak/.env` +
     `${HC_*}` shell expansion in `ExecStartPost=`, NOT by Python
     or PHP. They live in the model only so `levels validate-config`
     can flag a missing one at deploy time; `emit-config` writes
     them to the JSON for inventory; PHP never reads them.
     **`hc_recap` follow-up:** `systemd/kayak-recap.service` (added
     2026-05-14, commit 7c22068) doesn't yet ping healthchecks.
     Add `ExecStartPost=-/usr/bin/curl -fsS -m 10 --retry 3 -o
     /dev/null ${HC_RECAP}` in the same commit that adds the
     `hc_recap` field to `KayakConfig`.
   - Keep the module-level constants (`DATABASE_URL`, `OUTPUT_DIR`,
     etc.) as `KayakConfig`-derived attributes for source-compat:
     `_config = KayakConfig(); DATABASE_URL = str(_config.database_url)`.
     Every existing `from kayak.config import DATABASE_URL` keeps
     working. New code paths should call `KayakConfig()` (or
     `kayak.config.get_config()` helper) so test monkeypatching of
     env vars takes effect.
3. **0.3 — Tests.** `tests/test_config.py`:
   - Env-var override
   - Default values
   - Validation failure (invalid URL, out-of-range int)
   - `~/.config/kayak/.env` precedence over OS env (matches today's
     `python-dotenv` behavior)

**Verification gate:** full pytest suite green; no production behavior
change.

**Effort:** ~3 h.

**Risks:**
- Test late-env-binding: tests that `monkeypatch.setenv("DATABASE_URL",
  ...)` AFTER `import kayak.config` see the new value only via a fresh
  `KayakConfig()` call — `kayak.config.DATABASE_URL` was bound at
  import time. Already true today (`os.environ.get` at import); Phase
  0 preserves the semantics with a test that pins the contract.
- `pydantic[email]` adds `email-validator` (~150 KB); the audit's "no
  unnecessary new deps" stance is satisfied since this is the
  minimum required for `EmailStr`. Alternative: drop `EmailStr`,
  use `str` + a custom regex validator. Decision: take the dep; the
  validator is widely used and the cost is small.

### Phase 1 — `levels emit-config` + `levels show-config`

**Goal:** Python can write the JSON snapshot and dump the resolved
config. No PHP changes yet.

1. **1.1 — Subcommands.** Add `src/kayak/cli/emit_config.py` per the
   existing `addArgs(subparsers)` pattern. Two siblings:
   - `levels emit-config [--out PATH] [--dry-run]` — writes the
     JSON. Default `--out` is `/etc/kayak/runtime-config.json`.
     `--dry-run` writes to stdout.
   - `levels show-config [--format {table,json}]` — prints the
     resolved config to stdout (table by default for human eyes;
     JSON for diffing). Mirrors what `PHP Config::dump()` will do
     in Phase 4.3.
2. **1.2 — Atomic write.** `<path>.tmp` in the **same directory** as
   `<path>` (so `os.rename` is atomic — `tmpfile` + `rename` across
   filesystems is NOT atomic). Then `chmod 0640`. The `chown
   root:www-data` step requires root; see 1.5 for the sudo dance.
3. **1.3 — Content shape.** `KayakConfig().model_dump(mode="json",
   exclude_none=True)` gives the JSON; explicitly pull each
   `SecretStr` value through `.get_secret_value()` (the default
   `mode="json"` represents secrets as `"**********"`). The JSON
   file is mode 0640 root:www-data so secrets are acceptable; an
   in-file comment line at the top of `emit_config.py` calls this
   out.
4. **1.4 — Idempotence.** Read the existing file (if any) byte-for-
   byte; skip the write if equal. Emit one line either way (`"...:
   unchanged"` vs `"... updated"`) so `scripts/deploy.sh` output is
   self-explanatory. **Drift exclusion:** add
   `runtime-config.json` to `scripts/check-config-drift.sh`'s
   ignore list — it's a generated artifact, not a tracked file.
5. **1.5 — Integration with `scripts/deploy.sh`.** Add to deploy:
   - **Sudoers entry** (operator one-time setup, lands in
     `deploy/sudoers.d/kayak-emit-config` + a SETUP.md step):
     ```
     pat ALL=(root) NOPASSWD: /home/pat/.venv/bin/levels emit-config*
     ```
     Pin the command path so the sudo grant is narrow.
   - **Deploy step** inserted between `levels migrate` and `levels
     build`:
     ```
     sudo -n /home/pat/.venv/bin/levels emit-config --out
         /etc/kayak/runtime-config.json
     ```
   - **No php-fpm reload** is needed for JSON content changes (PHP
     re-reads the file once per request; the static-cache resets
     between requests in FPM). The deploy script's existing
     "systemd/nginx changed" NOTICE remains for actual `/etc/`
     config edits; the JSON path does NOT trigger that NOTICE.

**Verification gate:** `levels emit-config --dry-run` round-trips the
current config; running with `--out=/tmp/test.json` produces a
well-formed JSON the schema-parity test (Phase 2.3) can consume.
Reading the live file requires `sudo` (mode 0640 root:www-data); use
`sudo cat /etc/kayak/runtime-config.json` or `levels show-config`
for human inspection.

**Effort:** ~4 h.

**Risks:**
- `/etc/kayak/` may not exist on a fresh host. The sudo-emit-config
  step mkdir-with-parents (running as root, this is safe); without
  root we can't write `/etc/` at all, so the failure mode is loud.
- `KAYAK_HOME` not landed: hardcode `/etc/kayak/runtime-config.json`
  but add a `KAYAK_CONFIG_PATH` env override (read by `KayakConfig`'s
  reader, not the model itself) for tests + the future T3.4 swap.
- Sudoers entry breadth. The `*` glob in the sudoers spec covers
  `levels emit-config --dry-run`, `levels emit-config --out=X`, etc.
  Narrow enough for the purpose (any `levels emit-config` invocation
  is intended-by-design); widen the grant only if a future need
  surfaces.

### Phase 2 — PHP read path

**Goal:** PHP reads the JSON, falls back to env on miss.

1. **2.0 — Pre-flight: $_SERVER audit.** Before any migration, grep
   `php/` for `$_SERVER['XXX']` reads of any key we're moving
   (`EDITOR_FEATURE`, `TURNSTILE_SITE_KEY`, `MAIL_FROM`, `SITE_URL`,
   `SQLITE_PATH`). Any hit must migrate to `Config::*()` in the same
   commit as 2.2, because Phase 2.5 will delete the `fastcgi_param`
   lines that populate `$_SERVER`. `getenv()`-based reads survive
   the fastcgi_param removal (FPM-pool env channel persists);
   `$_SERVER` reads do not.
2. **2.1 — New `php/includes/config.php`.** A single class that:
   - Production singleton path: `/etc/kayak/runtime-config.json`.
     Loaded once per request lifecycle (static cache).
   - Test factory: `Config::for_test(string $path): Config` returns
     a non-singleton instance bound to a temp path so PHPUnit
     fixtures can isolate.
   - `Config::get(string $key, $default = null): mixed` returns the
     decoded JSON value or the env fallback.
   - Typed wrappers (the public API; PHPStan-friendly):
     `Config::str`, `Config::int`, `Config::bool`, `Config::list`,
     `Config::url`.
   - On file-missing or parse-failure, falls back to
     `getenv($key_UPPER)` (Python's `maintainer_emails` → PHP's
     `MAINTAINER_EMAIL`). Logs one WARN-severity line tagged
     `[CONFIG-FALLBACK]` per request via `error_log(...)`.  PHP-FPM
     captures `error_log` writes into the FPM master's error log;
     that stream is also picked up by journald for the
     `php-fpm.service` unit. Grep handle:
     `journalctl -u php-fpm | grep '\[CONFIG-FALLBACK\]'`.
3. **2.2 — Migrate callers.** One file per commit:
   - `php/includes/db.php` — `getenv('SQLITE_PATH')` →
     `Config::str('database_path')`. JSON carries the SQLite path
     too, not just the Python `database_url`; the emit step derives
     `database_path` by stripping the `sqlite:///` prefix.
   - `php/includes/auth.php` — `maintainer_emails()` reads
     `Config::list('maintainer_emails')` first. **The DB-rows
     fallback (`SELECT email FROM editor WHERE status='maintainer'`)
     stays** — it's not a coincidence, it's the documented "no env
     set" behavior. Only the hardcoded literal at the bottom of the
     function disappears (since the JSON always has at least an
     empty list, the literal can never be reached). PHP behavior:
     env → JSON → DB rows → empty-list-with-warn. PHPStan-friendly.
   - `php/includes/mail.php` — `MAIL_FROM`.
   - `php/includes/turnstile.php` — `TURNSTILE_SITE_KEY`,
     `TURNSTILE_SECRET`.
   - `php/comment.php`, `php/login.php`,
     `php/includes/propose_handler.php` — `EDITOR_FEATURE`,
     `SITE_URL` per their existing `getenv` calls.
   For each migrated key, the PHP code consults `Config::get()`
   first; the dual-read fallback covers the "operator hasn't run
   `emit-config` yet" case.
4. **2.3 — Schema parity test.** `tests/php/ConfigTest.php` (new):
   - Spawns `levels emit-config --out=$TMPDIR/runtime-config.json`
     against a known env (PHPUnit's `setUp` writes the env file,
     invokes the binary).
   - Binary path comes from `getenv('KAYAK_LEVELS_BIN')` with
     default `/home/pat/.venv/bin/levels`; CI sets the var to the
     runner-specific path.
   - PHP test then constructs `Config::for_test($tmpPath)`.
   - Asserts every documented key resolves to the expected value and
     typed shape.
   - Asserts the fallback path emits the expected
     `[CONFIG-FALLBACK]` log line when the JSON is absent.
5. **2.4 — PHPStan.** `Config::get()`'s return type is `mixed` by
   necessity; the typed wrappers (`str/int/bool/list/url`) are the
   PHPStan-friendly public API. PHPStan level 8 stays green from
   this commit on — no `phpstan-baseline.neon` entries.
6. **2.5 — nginx fastcgi_param cleanup.** Once PHP reads from JSON,
   the `fastcgi_param EDITOR_FEATURE $editor_feature` lines (and
   peers, plus the `SQLITE_PATH` line — that's in JSON too now) in
   `conf/snippets/levels-common.conf` are dead code. Even on a host
   where `set $editor_feature on;` exists outside the repo, dropping
   the `fastcgi_param` is safe: the FPM-pool env channel
   (`env[X]=$X` in `deploy/kayak-fpm-pool.conf`) still carries the
   same value through to `getenv()`. Delete all five lines. Keep
   only `SCRIPT_FILENAME` (PHP-FPM requirement) + `include
   fastcgi_params` (the Debian-default set).
   - Per [feedback_sudo_cp_clobbers_overrides] + [feedback_no_sudo]:
     the snippet edit lands in the repo first.
   - **Operator action:** diff against
     `/etc/nginx/snippets/levels-common.conf`, `sudo install` the
     new file, then `sudo nginx -t && sudo systemctl reload nginx`.
   - **Order:** ship 2.0/2.1/2.2/2.3/2.4 first; this is the last
     commit of Phase 2 and triggers the nginx reload.

**Verification gate:**
- All PHP integration tests still pass (`composer test`).
- PHPStan level 8 still clean.
- A manual `levels emit-config` followed by `curl
  https://levels.mousebrains.com/login.php` (or whatever surface
  reads the keys) confirms the JSON path is taken — verify by
  watching `journalctl -u php-fpm` for the absence of the fallback
  INFO line.
- `levels orphan-check` clean.
- Site visually unchanged (smoke screenshots if practical).

**Effort:** ~1 day (file-by-file PHP migration is the bulk).

**Risks:**
- A migrated PHP file that's served by an unmigrated cached opcache
  view could intermittently see one or the other source. Mitigation:
  `systemctl reload php-fpm` after each PHP-includes commit (the
  deploy script already does this for any pyproject.toml change;
  extend the trigger to include `php/includes/config.php` changes).
- The schema-parity test depends on `levels emit-config` being
  installable in CI. CI already has the venv built (per Phase 3.1
  / T2.1 pin); no new infra needed.
- PHP's `parse_ini_file` vs `json_decode` performance: `json_decode`
  on a ~2KB file is <1ms per request. Acceptable; no caching tier
  needed beyond the once-per-request static.

### Phase 3 — Validation hardening

**Goal:** the typed config catches misconfiguration at boot time.

1. **3.1 — Strict mode on the Python side.** Promote
   `KayakConfig.model_config = SettingsConfigDict(env_file=...,
   case_sensitive=False, validate_default=True)`. Pydantic-settings
   only inspects env vars matching declared field names, so
   `extra="forbid"` adds nothing for env reads (only for direct
   `KayakConfig(...)` kwarg construction). The real typo risk —
   `MAINTANER_EMAIL` silently producing the default — is caught by
   `levels validate-config --known-env` (3.2): it scans the OS env
   against an explicit **name allowlist** derived from the pydantic
   model's `Field.alias` + name set, plus a short curated extras
   list for the systemd-consumed `HC_*` URLs and `NTFY_TOPIC`. Any
   env var that looks config-ish (`MAINTAINER_*`, `SITE_*`,
   `EDITOR_*`, etc.) but doesn't match the allowlist produces a
   WARN. Prefix-only matching was rejected (iter 3) because
   `OUTPUT_FORMAT` and friends would false-positive.
2. **3.2 — `levels validate-config` subcommand.** Same as
   `emit-config --dry-run` but with `--strict` semantics. Exit
   codes: **0** = OK, **1** = validation failure (field invalid),
   **2** = unable-to-run (env file missing, unreadable model).
   Mirrors `scripts/health-check.sh`'s ladder. Optional
   `--known-env` flag enables the typo scan from iter 1 (E).
   - **Deploy.sh order** (cumulative through this phase):
     `git pull → (pip install if pyproject.toml changed) →
      levels validate-config → levels migrate → sudo -n levels
      emit-config → levels build → orphan-check`.
     `validate-config` runs AFTER `pip install` so the latest
     model is loaded; runs BEFORE `migrate` so a broken config
     fails the deploy without touching the DB.
3. **3.3 — PHP-side schema check.** When `Config` loads the JSON,
   compare the present keys against an embedded list of "expected
   keys" (compiled into PHP from the Python schema export). Missing
   keys → fall back to env for those keys + log a per-key WARN.
   Extra keys → silent (forward-compat).
4. **3.4 — CI gate.** Add a CI step that runs `levels validate-
   config` against a fixture env. Fails the build if a developer
   adds a new env var without registering it in `KayakConfig`.

**Verification gate:** CI fails when an unknown env var is set;
deploy fails when validation fails; PHP logs a clear WARN for any
missing-key fallback.

**Effort:** ~3 h.

**Risks:**
- `validate-config --known-env`'s prefix allowlist might miss a
  legitimate config var (`UV_CACHE_DIR`, for instance, is unrelated
  but starts with `U`). Mitigation: the prefix list is explicit
  and bounded; the WARN-not-FAIL behavior means a false positive
  costs a one-line log entry, not a deploy block.
- The PHP-side schema check assumes both sides agree on key names.
  Phase 0 keeps key names identical; Phase 2 migration is mechanical.
  Failure mode is a missing-key fallback, which is graceful.

### Phase 4 — Dual-read removal

**Goal:** delete the `getenv` fallback in PHP once the JSON path has
been load-bearing for at least one operator-confirmed deploy.

Gated on: the operator confirms (via `journalctl -u php-fpm | grep
"config-fallback"`) that the fallback INFO line hasn't fired in N
days for any key (where N is at least 14 days — one weekly recap
cycle + one weekly drift detection cycle).

1. **4.1 — Remove fallback.** `Config::get()` no longer reads
   `getenv()`; a missing JSON is a fatal error (E_USER_ERROR + a
   visible 500 page rather than a silent degradation).
2. **4.2 — Remove the embedded "expected keys" list.** The JSON IS
   the schema now; PHP can read whatever's there.
3. **4.3 — Add a `Config::dump()` PHP CLI helper.** Equivalent to
   `levels show-config` on the Python side. Helps incident response.
4. **4.4 — Drop the env-read shims in `php/includes/{auth,mail,
   turnstile}.php`** (the `getenv` helpers that wrapped fallbacks).
5. **4.5 — Prune `deploy/kayak-fpm-pool.conf` `env[X]=$X` lines.**
   For keys the JSON now carries (`MAIL_FROM`, `SITE_URL`,
   `EDITOR_FEATURE`, `TURNSTILE_SITE_KEY`), the FPM-pool re-export
   is dead code post-Phase-4. Remove those `env[X]=$X` lines.
   `TURNSTILE_SECRET` **stays** in the env channel as a
   defense-in-depth measure: the JSON file is mode 0640
   root:www-data (group-readable), env vars are per-process. Two
   channels for the one true secret is acceptable belt-and-suspenders.
   - **Operator action:** diff `/etc/php/$PHP_VER/fpm/pool.d/
     kayak.conf` against the repo; `sudo install` the new file;
     `sudo systemctl reload php-fpm`.

**Verification gate:** PHP can no longer start without a valid
runtime-config.json; the schema-parity test catches the negative
case (missing file → fatal error).

**Effort:** ~2 h.

**Risks:**
- Operator forgets to run `emit-config` post-deploy. Mitigation:
  `scripts/deploy.sh` calls `emit-config` unconditionally; ensure
  the same script always reaches that step (it already does, post
  Phase 1).
- A fresh-host install procedure now has a new mandatory step.
  Document in `deploy/SETUP.md` § "Install" between secrets.env and
  systemd timers.

## End-state checklist

When all five phases complete:

- [ ] `src/kayak/config.py` is a `pydantic-settings` model; no
      module-level `os.environ.get` calls remain.
- [ ] `levels emit-config`, `levels show-config`, `levels validate-
      config` exist and are documented in `levels --help`.
- [ ] `/etc/kayak/runtime-config.json` is the PHP source of truth;
      mode 0640 root:www-data. Atomic writes via same-dir tmp +
      rename.
- [ ] `deploy/sudoers.d/kayak-emit-config` installed; deploy.sh
      runs `sudo -n levels emit-config` automatically.
- [ ] `php/includes/config.php` exists; PHPStan level 8 clean; no
      `getenv` fallbacks remain.
- [ ] `php/includes/{auth,mail,turnstile,db}.php` and all consumers
      read via `Config::str/int/bool/list/url()` typed wrappers.
- [ ] `php/includes/auth.php::maintainer_emails()` preserves the
      DB-rows fallback (NOT just env → JSON → empty); the
      hardcoded literal is gone.
- [ ] `conf/snippets/levels-common.conf` no longer carries any of
      `SQLITE_PATH`, `EDITOR_FEATURE`, `TURNSTILE_SITE_KEY`,
      `MAIL_FROM`, `SITE_URL` as `fastcgi_param` lines (only
      `SCRIPT_FILENAME` + `include fastcgi_params` remain).
- [ ] `deploy/kayak-fpm-pool.conf`'s `env[X] = $X` re-export lines
      are pruned. Only `TURNSTILE_SECRET` survives (defense-in-
      depth — JSON 0640 group-readable vs env vars per-process; the
      JSON has it too but the env channel is the belt to the JSON's
      suspenders for the single live secret).
- [ ] `tests/php/ConfigTest.php` validates schema parity.
- [ ] `tests/test_config.py` covers env parsing, validation,
      defaults, and the late-binding test from Phase 0.
- [ ] `scripts/deploy.sh` runs `validate-config` → `emit-config` in
      order; no NOTICE for JSON content changes (php-fpm picks up
      automatically).
- [ ] `scripts/check-config-drift.sh` ignores `runtime-config.json`.
- [ ] `docs/operations.md` § Config: new section describing the
      JSON path, the emit/show/validate commands, the sudoers
      grant, and the "what to do when config drift is suspected"
      runbook.

## Effort tally

| Phase | Effort |
|---|---|
| 0 — pydantic-settings introduction | 3 h |
| 1 — `levels emit-config` + `show-config` | 4 h |
| 2 — PHP read path (incl. 2.0 $_SERVER audit) | 9 h |
| 3 — Validation hardening | 3 h |
| 4 — Dual-read removal + FPM-pool cleanup | 3 h |
| **Total** | **22 h ≈ 2.75 d** |

Audit estimate was 2 days; the schema-parity test, the nginx
fastcgi_param dead-code cleanup, the $_SERVER audit, and the
FPM-pool prune (all caught during plan iter 1–3) add 6 hours.

## Verification (end-to-end)

After Phase 4, a fresh deploy on a clean host succeeds via:

```bash
sudo install -D -m 0600 -o root -g www-data \
    deploy/secrets.env.example /etc/kayak/secrets.env
sudo -e /etc/kayak/secrets.env                # operator fills values
cd /home/pat/kayak && scripts/deploy.sh       # incl. emit-config
sudo systemctl reload php-fpm                 # operator action per NOTICE
```

Then:

```bash
levels show-config                            # round-trip Python view
curl -fsS https://levels.mousebrains.com/login.php > /dev/null
journalctl -u php-fpm --since '1 min ago' | grep -i config-fallback
# Expected: no output (fallback never fired)
```

## Reproduce

Read-only commands a subsequent session can run to re-verify the
current state described in § Why before any phase starts:

```bash
# Inventory of config read sites (Python)
grep -rn "os.environ\|load_dotenv\|getenv" src/kayak/

# PHP getenv sites
grep -rn "getenv\b" php/

# nginx fastcgi_param refs
grep -rn "fastcgi_param" conf/

# Confirm the unset nginx vars
grep -rn "set \$editor_feature\|set \$turnstile_site_key\|set \$mail_from\|set \$site_url" conf/ deploy/
#   (expected: no matches; that's the dead-code finding)

# secrets.env current keys
sudo cat /etc/kayak/secrets.env
```

## Out of scope (consciously deferred)

- **DB-table config.** `fetch_url`, `calc_expression`, `gauge`,
  `gauge_threshold` rows stay where they are. They're data, not
  config.
- **Hot reload.** PHP-FPM picks up new JSON only on next request
  (the once-per-request static cache); a long-running worker would
  need a SIGHUP or pool reload, which the deploy script flags but
  doesn't perform.
- **KAYAK_HOME indirection (T3.4).** When T3.4 lands, swap the
  hardcoded `/etc/kayak/runtime-config.json` to a `KAYAK_HOME`-
  relative path. Add a follow-up note in this plan's iter log when
  T3.4 starts.
- **Config history / diff dashboard.** Internal-dashboard scope
  (Phase 6.1 Tier 2.4 of production-discipline); orthogonal.

## Risks (overall)

- **Drift between schema export and PHP expectations.** Mitigation:
  schema-parity test (Phase 2.3) runs in CI; PHP's "expected keys"
  list is generated, not handwritten.
- **JSON file world-readable by accident.** Mitigation: `install -m
  0640` enforced in `levels emit-config`; the file's group is
  `www-data` (PHP-FPM only).
- **Operator deploys without re-running emit-config.** Phase 4
  closes this loop — the deploy script calls emit-config every run;
  dual-read fallback during Phases 1-3 means the worst case is a
  stale-but-consistent config until the next deploy.
- **Phase 0 introduces a hard pydantic-settings dep.** Tier 2.1
  (CI/prod pin) already covers Python version; `pydantic-settings`
  is in widespread use and adds ~50 KB to the venv. Acceptable.
- **PHP regressions from the migration.** Mitigation: composer tests
  + the Playwright editor-journey spec (T2.5) catch end-to-end
  flow breaks; PHPStan level 8 catches static typing regressions;
  the dual-read fallback covers the case where a single file is
  migrated but its consumers aren't.

---

End of v0 draft. Iterations follow.
