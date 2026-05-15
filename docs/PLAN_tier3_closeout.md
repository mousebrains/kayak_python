# PLAN: Tier 3 closeout — typed config spine (T3.3) + KAYAK_HOME indirection (T3.4) + dormant-schema decision (T3.5)

> **Status: Closed (2026-05-15).** All seven phases shipped + verified in
> prod. Final residual diverged from prediction (86 raw / 59 indirection-
> filtered vs. plan's 52) — see Phase 5.7's "Actual residual" table for
> the per-row reconciliation. Two latent bugs surfaced and were fixed
> during the operator-side install: (a) `/etc/kayak/runtime-config.json`
> was outside PHP-FPM `open_basedir` so every PHP page 500'd post-deploy
> until Phase-5-late commit `2c89518` added the path; (b) `levels emit-
> config` wasn't reading `/etc/kayak/secrets.env`, so the JSON had empty
> `turnstile_*` and captcha verification silently bypassed — fixed in
> commit `450fbe7`. Both fixes are reflected in `docs/operations.md`
> § Config. The follow-up plans (`PLAN_pre_release_followup.md` §§ T3.3 /
> T3.4 / T3.5, `PLAN_outstanding_followups.md` § Phase 4.2) all carry
> "Closed (2026-05-15)" banners pointing back here.
>
> **Drafted:** 2026-05-14 against `main` at `eaa51c8` (T3.3 only);
> **extended:** 2026-05-14 against `main` at `248b899` (added T3.4 + T3.5
> phases + iter log v2). Source plans:
> `docs/PLAN_pre_release_followup.md` §§ T3.3 / T3.4 / T3.5 (architecture
> audit ARCH-H7 / ARCH-H8 / ARCH-H10), `docs/PLAN_outstanding_followups.md`
> § Phase 4.
>
> **T3.3 iter log (Phases 0–4):**
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
> **T3.4 + T3.5 extension iter log (Phases 5–6):** see § Iter log v2
> below the existing phases — the v2 entries follow the same A–QQ /
> A2–… labeling convention as v1.
>
> Dates absolute. References `file:line` against current `main`.

## Scope

This plan covers the three remaining Tier-3 audit items from
`PLAN_pre_release_followup.md`. (T3.1 parser/IO decoupling and T3.2
pipeline DAG already shipped; T3.6 release.sh is bundled into
`PLAN_outstanding_followups.md` Phase 2 alongside T3.4's
`scripts/deploy.sh` consumer, separate from this plan's Phase 5
work on the `KAYAK_HOME` env-var indirection itself.) The three
items land sequenced as Phases 0–6 below:

| Item | Audit ref | Phases | Effort |
|---|---|---|---|
| **T3.3** typed config spine | ARCH-H7 | 0–4 | 22 h |
| **T3.4** `KAYAK_HOME` indirection | ARCH-H8 | 5 | 6 h |
| **T3.5** dormant-schema decision | ARCH-H10 | 6 | 0.5 h |

Total: **~28.5 h ≈ 3.5 days.**

T3.4 lands AFTER T3.3 because the only PHP `/home/pat` hit
(`php/csp-report.php:84`) migrates onto `Config::str('csp_log_path')`
— a Phase 2.2 entry. T3.5 is doc-only (the substantive cleanup
shipped in migration 0022, commit landing the table drop); this
plan formalizes the per-feature decision.

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

### T3.4 — `/home/pat` welded into the deploy surface (ARCH-H8)

`grep -rn '/home/pat' --include='*.php' --include='*.sh'
--include='*.service' --include='*.timer' --include='*.conf'
--include='*.example'` across `php/`, `scripts/`, `systemd/`,
`conf/`, `deploy/` returns **86 hits** (2026-05-14, filtered to
exclude comment-only lines). The audit's framing — "blocks
containerization, second host, or second maintainer's local dev"
— is real, but the *reach* of an env-var indirection is bounded
by file-format semantics the audit didn't account for:

- **systemd does NOT expand `${KAYAK_HOME}` in `WorkingDirectory=`,
  `EnvironmentFile=`, `ReadWritePaths=`.** Only `ExecStart=` /
  `ExecStartPost=` / `Environment=` do env-var expansion. The `%h`
  specifier resolves to `/root` for system services regardless of
  `User=pat` (per `systemd.unit(5)`: "Note that this setting is not
  influenced by the `User=` setting"). So 35 of the 86 hits sit in
  unit-file directives (12 `WorkingDirectory` + 13 `EnvironmentFile`
  + 10 `ReadWritePaths`) that can't be parameterized today.
- nginx cannot read systemd's environment; its 3 path literals
  (`root`, favicon `alias`, security.txt `alias` in
  `levels-common.conf`) stay literal. The 11 `fastcgi_param
  SQLITE_PATH /home/pat/DB/kayak.db` lines disappear regardless,
  via T3.3 Phase 2.5's fastcgi_param cleanup.
- **PHP-FPM pool config does NOT expand env vars in directive
  values.** `deploy/kayak-fpm-pool.conf:38`'s `open_basedir`
  colon-list stays literal (4 distinct `/home/pat/...` path refs
  on one line).

The achievable reduction is "47 hits cleaned, 39 literal" — a
55% filtered reduction (excluding the new `KAYAK_HOME=` /
`Environment=KAYAK_HOME=` indirection lines, which are the
parameterization itself). The remaining literals each get a
one-line in-file comment explaining the file-format constraint,
so a future reader doesn't waste cycles re-discovering the same
fact.

### T3.5 — dormant-schema audit, partially closed (ARCH-H10)

Migration 0022 (`data/db/migrations/0022_drop_dormant_features.sql`,
2026-05-14) dropped one of the audit's four candidates and
deliberately preserved the other three:

| Candidate | Status | Why |
|---|---|---|
| `maintainer_credential` table | DROPPED | WebAuthn schema, never wired |
| `ChangeStatus.auto_applied` enum value | KEPT | Removing it shrinks SQLAlchemy-emit VARCHAR(11)→VARCHAR(6), trips the T2.3 schema-parity test against the live DB's VARCHAR(11) column without a table-rebuild migration |
| `ChangeTarget.trip_report` enum value | KEPT | Same VARCHAR-length reason |
| `EditorStatus.minimal` tier | KEPT | Audit was wrong: `admin.php` promotes `pending→minimal`, `propose_handler.php` has a `minimal`-specific daily cap (10/day), live DB has 1 editor at this tier |

The substance is settled. The remaining work is to (a) update
`PLAN_pre_release_followup.md` § T3.5's per-feature table to mark
each row Final with the rationale, (b) flip
`PLAN_outstanding_followups.md` § Phase 4.2 from "(partial)" to
"(closed)", and (c) put the audit-vs-reality decision matrix into
`docs/operations.md` § Schema decisions so future audits can
re-derive the call without re-reading the migration body.

## Goal

The plan has three sub-goals — one per audit item.

### T3.3 — typed config spine (Phases 0–4)

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

Out of scope for T3.3: DB-table config (`fetch_url` etc.) and
hot-reload (config changes still need a deploy + FPM-pool reload).
KAYAK_HOME indirection lands in Phase 5 (next sub-goal).

### T3.4 — `KAYAK_HOME` indirection (Phase 5)

A bounded reduction of the `/home/pat` literal surface such that:

1. **One env-var anchor.** `/etc/kayak/env` (NEW file, mode 0644
   root:root, world-readable, NOT secret) carries `KAYAK_HOME=/home/pat`.
2. **systemd `ExecStart=` substitution.** Every `kayak-*.service`'s
   `ExecStart=/home/pat/.venv/bin/levels …` rewrites to
   `${KAYAK_HOME}/.venv/bin/levels …`. Same for `ExecStart=` lines
   that invoke an in-tree helper script. `Environment=KAYAK_HOME=/home/pat`
   ships as a baked default ahead of `EnvironmentFile=-/etc/kayak/env`,
   so a unit on a host without `/etc/kayak/env` still has a defined
   value (no expansion-to-empty bug).
3. **Shell scripts source `/etc/kayak/env`.** `scripts/*.sh` and
   `systemd/*.sh` pick up `${KAYAK_HOME}` via `[ -r /etc/kayak/env ]
   && . /etc/kayak/env` plus a `: "${KAYAK_HOME:=/home/pat}"` default
   above it (works in dev shells that haven't installed the file).
4. **PHP picks up the one CSP-log path via runtime-config.json.**
   `php/csp-report.php:84`'s `/home/pat/logs/csp.log` literal moves
   onto `Config::str('csp_log_path')`. T3.3 Phase 0.2's `KayakConfig`
   gains a `csp_log_path: Path` field, default `/home/pat/logs/csp.log`.
5. **systemd directives that can't expand env vars stay literal —
   with a comment.** `WorkingDirectory=`, `EnvironmentFile=`,
   `ReadWritePaths=` each carry a leading comment in the unit file
   noting the systemd constraint, so a reader doesn't waste cycles
   re-discovering it.
6. **nginx paths stay literal — with a comment.** `root` +
   `alias` lines in `levels-common.conf` get a comment block at the
   top documenting the rationale (nginx can't read systemd env;
   `set $kayak_home ...;` adds fragility per server block).

The audit's original acceptance criterion ("`grep -rn '/home/pat'`
returns only `KAYAK_HOME=` lines") is **not achievable today**; the
revised criterion is "every reach-able literal is parameterized; each
remaining literal is annotated."

Out of scope for T3.4: containerization (a downstream consumer of
`KAYAK_HOME`, not a prereq); multi-host support (same).

### T3.5 — dormant-schema decision (Phase 6)

Doc-only closeout: formalize the per-feature decisions migration 0022
already executed. After this phase:

1. `PLAN_pre_release_followup.md` § T3.5's per-feature table is
   updated row-by-row with the Final decision and the rationale (the
   VARCHAR-length argument for the two enum values, the
   audit-was-wrong argument for `EditorStatus.minimal`).
2. `PLAN_outstanding_followups.md` § Phase 4.2 status flips from
   "(partial)" to "(closed)".
3. `docs/operations.md` § Schema decisions (NEW subsection) names
   the four candidates, the call for each, and a pointer to migration
   0022's commit body as the load-bearing record.

Out of scope for T3.5: re-litigating the keep/drop decisions. The
substance is settled; this phase is filing.

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
- **JSON path stays `/etc/kayak/runtime-config.json`.** Even with
  T3.4 in scope, `/etc/kayak/` is a system-wide directory unaffected
  by the user's home; `KAYAK_HOME` parameterizes paths under `$HOME`,
  not `/etc/`. The repo path inside `src/kayak/config.py` continues
  to derive from `Path(__file__).resolve().parents[2]`, which is
  symlink-safe and doesn't depend on the env.
- **systemd directive limits are load-bearing for T3.4 scope
  reduction.** `WorkingDirectory=`, `EnvironmentFile=`,
  `ReadWritePaths=` do NOT expand `${VAR}`; specifier `%h` resolves
  to `/root` regardless of `User=`. This is not a bug fixable in
  this plan — the systemd authors made the call deliberately
  (mounting/cgroup setup runs before env evaluation). The Phase 5
  plan respects the limit instead of fighting it.

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
- **`/etc/kayak/env` is non-secret and root:root 0644.** Mirrors the
  split `/etc/kayak/secrets.env` already implements: secrets live in
  the 0600 root:www-data file; deploy-time path indirection lives in
  the world-readable file. Keeping them split means a shell user
  (e.g., `pat`) can `cat /etc/kayak/env` to debug a path without
  needing sudo; sudo is still needed to read the secrets file.
- **`/etc/kayak/env` lands via `deploy/install-secrets.sh` (renamed
  to `install-config.sh` after this PR).** The script already installs
  `/etc/kayak/secrets.env` + the FPM pool overlay + the systemd
  drop-in; adding one more `install -D` line is the smallest path
  that keeps the install dance in one place. The drift detector's
  manifest grows by one entry.
- **`Environment=KAYAK_HOME=/home/pat` in every unit is a deliberate
  resilience floor, not config duplication.** Without it, a unit on a
  host that hasn't installed `/etc/kayak/env` would expand `${KAYAK_HOME}`
  to empty and `ExecStart=/.venv/bin/levels …` would fail noisily —
  fine in CI but ugly on a fresh install. The 12 duplicate lines are
  cheap; a maintainer who wants `KAYAK_HOME=/opt/kayak` must edit
  `/etc/kayak/env` (which takes precedence per `EnvironmentFile=`
  merge semantics — see `systemd.exec(5)` "if a variable has been set
  in `Environment=` and `EnvironmentFile=`, the latter overrides").
- **T3.5 closeout is doc-only.** The substantive cleanup landed in
  migration 0022. This phase formalizes the per-feature decisions
  in the source plans and `docs/operations.md`; no code or migration
  changes. Rationale: removing the two kept enum values would require
  a SQLite table-rebuild migration (`CREATE TABLE foo_new; INSERT
  INTO foo_new SELECT FROM foo; DROP TABLE foo; ALTER TABLE foo_new
  RENAME TO foo`) under `@no_transaction`, run only during a
  maintenance window — high cost for a purely cosmetic VARCHAR-length
  reduction.

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
- The JSON path stays `/etc/kayak/runtime-config.json` regardless
  of Phase 5 (T3.4). Add a `KAYAK_CONFIG_PATH` env override (read by
  `KayakConfig`'s reader, not the model itself) so tests can point at
  a temp file; production callers never set this.
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
   - `php/csp-report.php:84` — `/home/pat/logs/csp.log` literal moves
     onto `Config::str('csp_log_path')`. Added via Phase 5 (T3.4) —
     listed here so the Phase 2.2 migration sweep includes it in
     one pass rather than requiring a follow-up commit.
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

### Phase 5 — `KAYAK_HOME` indirection (T3.4)

**Goal:** reduce the `/home/pat` literal surface from 86 hits to 52
(39 once you exclude the new `KAYAK_HOME=` / `Environment=KAYAK_HOME=`
indirection lines, which are the parameterization itself). That is
a 55% reduction in the irreducible literal count; the remaining 39
sit in systemd directives that don't expand env vars
(`WorkingDirectory=`, `EnvironmentFile=`, `ReadWritePaths=`) or in
file formats whose own grammar disallows env-var expansion (nginx
`root` / `alias`, PHP-FPM pool `php_admin_value[open_basedir]`).
Each remaining literal carries a leading-comment annotation so a
reader sees why it stays.

**Depends on:** mostly nothing. The `csp-report.php` migration
(Phase 5.5) is the only piece with a hard dependency on T3.3 — it
needs `Config::str()` from T3.3 Phase 2.1, and per iter v2.1 (D2/L2)
that migration actually ships INSIDE T3.3 Phase 2.2's commit, not
as a standalone Phase 5 commit. The rest of Phase 5 (5.1 template,
5.2 install dance, 5.3 systemd `ExecStart=`, 5.4 shell scripts, 5.6
nginx + open_basedir comments, 5.7 verification) is independent of
T3.3 and can land before, during, or after T3.3 as the operator
prefers. The "linear after T3.3" sequencing in the Effort tally is
for review cohesion (one Tier-3-closeout PR sequence), not technical
necessity.

1. **5.1 — `/etc/kayak/env` template.** Repo file:
   `deploy/kayak-env.example`. Contents:

   ```
   # /etc/kayak/env — non-secret deploy-time configuration
   # Install: sudo install -D -m 0644 -o root -g root \
   #     deploy/kayak-env.example /etc/kayak/env
   # After edits: no daemon-reload needed (EnvironmentFile content is
   # re-read at each service start). kayak-*.service units are
   # Type=oneshot, so the next timer firing picks up the change. A
   # long-running service (none today) would need an explicit restart.
   #
   # KAYAK_HOME parameterizes paths under the operator's $HOME. It
   # CANNOT be used in systemd WorkingDirectory=/EnvironmentFile=/
   # ReadWritePaths= directives (systemd 257 does not expand env vars
   # in those — paths there stay literal). Use it in ExecStart= /
   # ExecStartPost= / shell scripts.
   KAYAK_HOME=/home/pat
   ```

   - Mode 0644 root:root. World-readable: this is path indirection,
     not a secret.
   - Drift detector manifest gains the line
     `deploy/kayak-env.example<TAB>/etc/kayak/env`.

2. **5.2 — Install dance.** `deploy/install-secrets.sh` renames to
   `deploy/install-config.sh` (`git mv`) and gains a step ahead of
   the existing secrets handling:

   ```bash
   ENV_FILE=/etc/kayak/env
   if [[ ! -e "$ENV_FILE" ]]; then
       say "installing kayak env file at $ENV_FILE"
       install -D -m 0644 -o root -g root \
           "$DEPLOY_DIR/kayak-env.example" "$ENV_FILE"
   else
       say "kayak env file already present at $ENV_FILE (leaving untouched)"
   fi
   ```

   Update `deploy/SETUP.md` to reflect the rename + new step.
   References to `install-secrets.sh` elsewhere
   (`grep -rn install-secrets.sh`): `deploy/SETUP.md`, possibly
   `docs/operations.md`, possibly `deploy/install-secrets.sh`'s own
   internal `Usage:` block. Sweep them in the same commit as the
   `git mv`.

3. **5.3 — systemd `ExecStart=` parameterization.** For every
   `kayak-*.service`:

   - Add as the first `[Service]` directive (before existing
     `EnvironmentFile=` lines):

     ```
     Environment=KAYAK_HOME=/home/pat
     EnvironmentFile=-/etc/kayak/env
     ```

     The in-unit `Environment=` line is a fallback floor — if
     `/etc/kayak/env` is missing on a fresh host, ExecStart still
     gets a defined value. When the file exists, its value wins per
     systemd merge semantics.
   - Rewrite `ExecStart=/home/pat/.venv/bin/levels <cmd>` to
     `ExecStart=${KAYAK_HOME}/.venv/bin/levels <cmd>`.
   - Same for `ExecStart=/home/pat/kayak/<helper>.sh` →
     `${KAYAK_HOME}/kayak/<helper>.sh`.
   - Add a leading comment block to each modified unit:

     ```
     # Paths in WorkingDirectory=/EnvironmentFile=/ReadWritePaths=
     # are LITERAL because systemd does not expand $VAR in those
     # directives. ExecStart=/ExecStartPost= use ${KAYAK_HOME} (set
     # by /etc/kayak/env with a fallback default above).
     ```
   - `systemd/install.service.sh` needs no change: the `cmp -s` byte
     comparison triggers daemon-reload on any unit-file content
     change, including the new `Environment=` + `EnvironmentFile=`
     lines.

4. **5.4 — Shell scripts: `source /etc/kayak/env`.** Standard prologue
   after `set -euo pipefail`:

   ```bash
   : "${KAYAK_HOME:=/home/pat}"
   [ -r /etc/kayak/env ] && . /etc/kayak/env
   ```

   The `:=` default keeps dev shells working without `/etc/kayak/env`.
   The `[ -r … ] && …` guard means a fresh host without the file
   doesn't crash the script before it can even report the absence.

   Files to edit (each gets the prologue + `${KAYAK_HOME}` replacement
   for path constants):

   - `scripts/snapshot_metadata.sh:16-17` — `REPO`, `VENV_PY`.
   - `scripts/deploy.sh:23-25` — `REPO`, `VENV_PIP`, `LEVELS`.
   - `scripts/db_push.sh:18-19`, `scripts/db_pull.sh:15-16` —
     `REMOTE_DB`, `REMOTE_BACKUP_DIR` (already use
     `${VAR:-/home/pat/...}`; just swap the default to
     `${VAR:-${KAYAK_HOME}/...}`).
   - `scripts/check-config-drift.sh:21` — `REPO`
     (`${REPO:-/home/pat/kayak}` → `${REPO:-${KAYAK_HOME}/kayak}`).
   - `scripts/regenerate_schema_svg.sh:21` — `VENV`.
   - `systemd/kayak-heartbeat.sh:18` — `DB`.
   - `systemd/kayak-backup-hourly.sh:23-24`,
     `kayak-backup-weekly.sh:21-22`, `kayak-backup-offsite.sh:14` —
     `DB`, `BACKUP_DIR`.
   - `systemd/kayak-recap.sh:21` — `RECAP=$("${KAYAK_HOME}/.venv/bin/
     python3" "${KAYAK_HOME}/kayak/scripts/recap.py" …)`.

5. **5.5 — PHP `csp-report.php` migration.** Two coordinated edits:

   - **T3.3 Phase 0.2's `KayakConfig` model** gains a field:

     ```python
     csp_log_path: Path = Path("/home/pat/logs/csp.log")
     ```

     Land this in the same commit that ships T3.3 Phase 0.
   - **T3.3 Phase 2.2's per-file migration list** already includes
     `php/csp-report.php` (see § Phase 2.2 above — added during
     this plan's iter v2.1). When T3.3 Phase 2.2 lands, the
     csp-report migration ships with the other PHP files; no
     separate Phase 5.5 commit is needed.

   This entry stays in Phase 5 because the field-add (KayakConfig)
   is conceptually a T3.4 surface reduction even though the
   landing happens during T3.3's commits.

6. **5.6 — nginx + PHP-FPM open_basedir (documentation only).**
   Two literal-path surfaces survive Phase 5 unchanged, each with a
   leading-comment annotation:

   **(a) `conf/snippets/levels-common.conf` nginx paths** — add a
   comment block at the top of the snippet:

   ```
   # PATH LITERALS NOTE: the `root /home/pat/public_html;` line and
   # the two `alias /home/pat/...` lines (favicon, security.txt) are
   # LITERAL. nginx cannot read systemd's environment, and a
   # `set $kayak_home ...;` plus `$kayak_home/...` rewrite adds
   # per-server-block fragility (every server block needs the `set`,
   # easy to miss when adding a new block). KAYAK_HOME indirection
   # (T3.4) deliberately stops at this file.
   ```

   The `fastcgi_param SQLITE_PATH /home/pat/DB/kayak.db` lines (11
   of them) disappear via T3.3 Phase 2.5's cleanup — `database_path`
   lands in the JSON.

   **(b) `deploy/kayak-fpm-pool.conf` open_basedir directive** —
   line 38 carries
   `php_admin_value[open_basedir] = /home/pat/public_html:/home/pat/kayak/php:/home/pat/DB:/home/pat/logs`.
   PHP-FPM does not expand env vars in pool config values. Add a
   comment immediately above the directive:

   ```
   # open_basedir is a literal-path security control. PHP-FPM pool
   # config does not expand $VAR. If KAYAK_HOME changes from
   # /home/pat, edit each colon-separated entry by hand. The
   # restriction is intentional (containing PHP file IO); the
   # literal-path requirement is a PHP-FPM limit, not a kayak
   # design choice.
   ```

7. **5.7 — Verification gate.** Run the surface grep and confirm
   the residual count:

   ```bash
   grep -rn '/home/pat' php/ scripts/ systemd/ conf/ deploy/ \
       --include='*.php' --include='*.sh' \
       --include='*.service' --include='*.timer' \
       --include='*.conf' --include='*.example' \
     | grep -v '^[^:]*:[0-9]*:[[:space:]]*#'
   ```

   Baseline today (2026-05-14, pre-Phase-5): **86 hits**, distributed
   as ~50 systemd .service directive lines (12 `WorkingDirectory`,
   14 `EnvironmentFile`, 10 `ReadWritePaths`, 13 `ExecStart`, plus
   one notify-failure `EnvironmentFile`); ~14 nginx (1 `root`, 2
   `alias`, 11 `fastcgi_param SQLITE_PATH`); ~13 shell-script
   path constants; 1 PHP CSP-log path; 1 PHP-FPM open_basedir
   directive in `deploy/kayak-fpm-pool.conf`.

   Expected matches after Phase 5 (with T3.3 Phase 2.5's
   fastcgi_param cleanup also landed):

   | Kind | Count | Files |
   |---|---|---|
   | `WorkingDirectory=/home/pat/kayak` | 12 | every `kayak-*.service` |
   | `EnvironmentFile=/home/pat/.config/kayak/.env` | 13 | every `kayak-*.service` + `notify-failure` |
   | `ReadWritePaths=/home/pat/...` | 10 | a subset of `kayak-*.service` |
   | `root /home/pat/public_html;` | 1 | `levels-common.conf` |
   | `alias /home/pat/...` | 2 | `levels-common.conf` (favicon, security.txt) |
   | `open_basedir` colon-list with 4 `/home/pat/...` entries | 1 line, 4 path refs | `deploy/kayak-fpm-pool.conf` |
   | `KAYAK_HOME=/home/pat` (template) | 1 | `deploy/kayak-env.example` |
   | `Environment=KAYAK_HOME=/home/pat` (in-unit floor) | 12 | every `kayak-*.service` |
   | **Total residual** | **52** | down from 86 today |

   - **Indirection-line filtered count** (excluding the
     `KAYAK_HOME=` / `Environment=KAYAK_HOME=` lines that ARE the
     parameterization, not the problem): **39** — a 55% reduction
     from today's 86.
   - **The 11 `fastcgi_param SQLITE_PATH` lines** drop independently
     via T3.3 Phase 2.5. The table above assumes T3.3 P2.5 has
     landed; if Phase 5 ships first, the residual is 63 unfiltered
     (52 + 11) / 50 filtered (39 + 11). The 11 disappear when T3.3
     P2.5 ships, bringing residual to the 52 / 39 numbers above.
   - **The audit's original "only `KAYAK_HOME=` lines remain"
     criterion is consciously unmet.** Systemd's
     `WorkingDirectory=` / `EnvironmentFile=` / `ReadWritePaths=`
     don't expand env vars; specifier `%h` resolves to `/root` for
     system services. Each remaining literal in those directives
     carries the leading-comment annotation from § 5.3, so a
     reader doesn't waste cycles re-discovering the constraint.

   **Actual residual after Phase 5.4 lands (2026-05-15): 86** —
   distributed as:

   | Kind | Predicted | Actual | Delta / cause |
   |---|---|---|---|
   | `WorkingDirectory=` | 12 | 12 | — |
   | `EnvironmentFile=` | 13 | 15 | +2 (three services added since plan: `cert-expiry`, `cert-renewal-test`, `config-drift`; one carries no env file) |
   | `ReadWritePaths=` | 10 | 10 | — |
   | `ExecStart=/home/pat/...` | 0 | 13 | **+13 — systemd 257 rejected `${KAYAK_HOME}/.venv/bin/levels` as binary path** ("Neither a valid executable name nor an absolute path" — systemd.exec(5): "the first argument may not be a variable"). Phase 5.3 reverted ExecStart binary literals; ExecStart arguments and ExecStartPost= still expand. |
   | `Environment=KAYAK_HOME=/home/pat` | 12 | 15 | +3 (same three-service drift) |
   | `root /home/pat/public_html;` | 1 | 1 | — |
   | `alias /home/pat/...` | 2 | 2 | — |
   | `open_basedir` (1 line, 4 path entries) | 1 | 1 | — |
   | `KAYAK_HOME=/home/pat` (template) | 1 | 1 | — |
   | Shell-script `: "${KAYAK_HOME:=/home/pat}"` prologue defaults | (not enumerated) | 11 | new indirection floor — one per script that sources `/etc/kayak/env` |
   | PHP docstrings + Config default fallback | (not enumerated) | 5 | 2× `show-config.php` invocation examples, 1× `db.php` layout docstring, 1× `csp-report.php` docstring, 1× `Config::str('csp_log_path', '/home/pat/logs/csp.log')` fallback default |
   | **Total residual** | **52** | **86** | **+34** |

   Indirection-line filtered count (excluding the 27 lines that
   ARE the parameterization — 15 `Environment=KAYAK_HOME=`, 11
   shell-prologue defaults, 1 `env.example`) is **59**.

   The two newly-discovered constraints relative to the plan's
   prediction — `ExecStart=` binary literal and PHP-FPM
   `open_basedir` literal — both surface as "stays literal with
   leading-comment annotation" (§ 5.3 for ExecStart, § 5.6 for
   open_basedir). The reduction is meaningful but smaller than the
   plan estimated: 86 → 86 raw / 59 filtered, where the gain is
   primarily *semantic* — the literal paths now sit next to
   comments that explain why they can't be parameterized, so a
   future operator relocating `KAYAK_HOME` sees the constraint
   inline instead of inferring it.

**Effort:** ~6 h.
- 0.5h template file (`deploy/kayak-env.example`)
- 1.5h systemd unit edits (12 services × 4 small edits each, mechanical)
- 1.5h shell-script edits (10 files × prologue + path swaps)
- 0.5h PHP `csp-report.php` + `KayakConfig` field (depends on T3.3 P2)
- 0.5h nginx comment block + drift-manifest entry
- 0.5h `install-config.sh` rename + new step + SETUP.md update
- 0.5h verification grep + commit messaging
- 0.5h deploy + smoke (one `kayak-*.service` start, confirm
  `${KAYAK_HOME}` expanded)

**Risks:**

- **systemd reload ordering.** Adding `Environment=` +
  `EnvironmentFile=-/etc/kayak/env` to a unit and `daemon-reload`-ing
  triggers a unit-file reread but does NOT restart running services.
  A service started before the reload retains the old env. Confirm
  via `systemctl show kayak-pipeline | grep KAYAK_HOME` — if empty,
  restart the service.
- **`EnvironmentFile=-` vs `EnvironmentFile=`.** The dash makes the
  file optional. We keep the dash because dev hosts won't have it.
  Risk: a typo'd `/etc/kayak/env` (e.g., `KAYAK_HMOE=...`) silently
  fails to override the in-unit default. Mitigation: the smoke
  step in 5.8 catches this for at least one unit; routine deploys
  ensure the deploy-time `systemctl show` introspection.
- **`/etc/kayak/env` ownership drift.** A future operator running
  `sudo -e /etc/kayak/env` keeps the existing ownership/mode
  (`sudoedit` preserves these). A `chmod 0600` accident would
  break systemd reads — install-config.sh's idempotent install
  step re-enforces 0644 on every run.
- **Search-replace miss.** Mechanical edits across 15+ files always
  carry a "I missed one" risk. Verification grep catches the typical
  shapes (`/home/pat/`); residual shapes (escaped, in a heredoc,
  etc.) are unlikely given the file types but warrant a manual
  scan pass.

### Phase 6 — T3.5 dormant-schema decision (doc-only)

**Goal:** formalize the per-feature decisions migration 0022 already
executed. No code or migration changes; this is a filing pass that
makes the decision searchable in the same places future audits will
look.

**Depends on:** nothing. Could land before Phase 0 in principle;
ordered last because it's the smallest and the iter-log convention
keeps all related Tier-3 work in one plan.

1. **6.1 — `PLAN_pre_release_followup.md` § T3.5 update.** Rewrite
   the per-feature decision table to reflect what shipped:

   ```markdown
   | Feature | Decision | Justification |
   |---|---|---|
   | `rating` / `rating_data` tables + `calc-rating` step | KEEP | Documented dormant in `CLAUDE.md`; reserved for per-gauge rating curves. No active maintenance cost beyond presence. |
   | `MaintainerCredential` (WebAuthn schema) | DROPPED in 0022 | Schema only; no register/assert code. CASCADE on `delete_editor` had nothing to cascade. |
   | `ChangeRequestAttachment` (photo uploads) | KEEP | Documented as "Phase 2+" pending; FPM upload limit pre-blocks abuse. |
   | `ChangeStatus.auto_applied` enum value | KEEP | Removing the value shrinks SQLAlchemy-emit VARCHAR(11)→VARCHAR(6); the live DB's column is VARCHAR(11). A schema-parity-clean removal needs a table-rebuild migration under `@no_transaction` — cosmetic-only gain. Documented in 0022's commit body. |
   | `ChangeTarget.trip_report` enum value | KEEP | Same VARCHAR-length reason. |
   | `EditorStatus.minimal` tier | KEEP | Audit was wrong: `admin.php` promotes `pending→minimal` (first review step), `propose_handler.php` has a `minimal`-specific daily cap (10/day), live DB has 1 editor at this tier. |
   ```

   Mark the section "Status: Closed (DATE)" at the top, where DATE
   is the day Phase 6.1 actually lands (not the plan-drafting date).

2. **6.2 — `PLAN_outstanding_followups.md` § Phase 4 table update.**
   Row 4.2 today reads
   `| 4.2 | T3.5 — Dormant schema cleanup | 1d | T2.3 schema parity (Phase 3.4) |`.
   Update to
   `| 4.2 | T3.5 — Dormant schema cleanup (closed) | 0d | T2.3 done; see notes |`
   and append a one-line pointer after the table (or as a footnote):

   ```
   See `PLAN_tier3_closeout.md` § Phase 6 and migration
   `data/db/migrations/0022_drop_dormant_features.sql` for the
   per-feature rationale; the "1d" budget was the audit estimate
   pre-decision and is no longer needed.
   ```

   (The "(partial)" wording lives in the TaskList task #32 only;
   the PLAN file's row never carried that suffix.)

3. **6.3 — `docs/operations.md` § Schema decisions (NEW
   subsection).** Add a top-level section (peer of § Rollback,
   § Bus-factor partner, etc.) summarizing:
   - The audit-vs-reality split: the audit (ARCH-H10) flagged **four**
     candidates for removal — `maintainer_credential`,
     `ChangeStatus.auto_applied`, `ChangeTarget.trip_report`, and
     `EditorStatus.minimal`. Migration 0022 dropped one
     (`maintainer_credential`); the other three were retained, with
     reasons (VARCHAR-length for the two enum values; audit was wrong
     for `minimal`). The other two rows in Phase 6.1's table
     (`rating`/`rating_data` and `ChangeRequestAttachment`) were
     audit-flagged as "schema-only carry cost" candidates but with
     KEEP justifications baked in — they're listed for completeness,
     not because they were ever in flux.
   - When VARCHAR-length sensitivity matters (it gates the
     schema-parity test in `tests/test_db/test_schema_parity.py`).
   - The "if you want to drop a kept enum value later" recipe
     (table-rebuild migration template + `@no_transaction`
     decorator + maintenance-window scheduling pointer).

**Effort:** ~30 min.
- 5m PLAN_pre_release_followup.md edit
- 5m PLAN_outstanding_followups.md edit
- 15m docs/operations.md new section
- 5m commit + verification grep: `grep -rn 'Dormant schema cleanup' docs/` should show the "(closed)" annotation in both PLAN files; `grep -rn '(partial)' docs/` should return 0 hits (the suffix was never in the PLAN files — see iter v2.5 finding Y2).

**Risks:** none load-bearing. Worst case: a future audit re-reads
the migration body anyway because the operations.md section is
tucked too far down — mitigated by the cross-reference in the
PLAN_*.md files.

## End-state checklist

When all seven phases complete:

### T3.3 (Phases 0–4)

- [x] `src/kayak/config.py` is a `pydantic-settings` model; no
      module-level `os.environ.get` calls remain. (The dead
      `MAINTAINER_EMAIL` module constant was removed in the
      2026-05-15 closeout.)
- [x] `levels emit-config`, `levels show-config`, `levels validate-
      config` exist and are documented in `levels --help`.
- [x] `/etc/kayak/runtime-config.json` is the PHP source of truth;
      mode 0640 root:www-data. Atomic writes via same-dir tmp +
      rename.
- [x] `deploy/sudoers.d/kayak-emit-config` installed; deploy.sh
      runs `sudo -n levels emit-config` automatically.
- [x] `php/includes/config.php` exists; PHPStan level 8 clean; no
      `getenv` fallbacks remain.
- [x] `php/includes/{auth,mail,turnstile,db}.php` and all consumers
      read via `Config::str/int/bool/list/url()` typed wrappers.
- [x] `php/includes/auth.php::maintainer_emails()` preserves the
      DB-rows fallback (NOT just env → JSON → empty); the
      hardcoded literal is gone.
- [x] `conf/snippets/levels-common.conf` no longer carries any of
      `SQLITE_PATH`, `EDITOR_FEATURE`, `TURNSTILE_SITE_KEY`,
      `MAIL_FROM`, `SITE_URL` as `fastcgi_param` lines (only
      `SCRIPT_FILENAME` + `include fastcgi_params` remain).
- [x] `deploy/kayak-fpm-pool.conf`'s `env[X] = $X` re-export lines
      are pruned. Only `TURNSTILE_SECRET` survives (defense-in-
      depth — JSON 0640 group-readable vs env vars per-process; the
      JSON has it too but the env channel is the belt to the JSON's
      suspenders for the single live secret).
- [x] `tests/php/ConfigTest.php` validates schema parity.
- [x] `tests/test_config.py` covers env parsing, validation,
      defaults, and the late-binding test from Phase 0.
- [x] `scripts/deploy.sh` runs `validate-config` → `emit-config` in
      order; no NOTICE for JSON content changes (php-fpm picks up
      automatically).
- [x] `scripts/check-config-drift.sh` ignores `runtime-config.json`
      (the file is not in the opt-in manifest, so the drift checker
      never compares it).
- [x] `docs/operations.md` § Config: new section describing the
      JSON path, the emit/show/validate commands, the sudoers
      grant, and the "what to do when config drift is suspected"
      runbook.

### T3.4 (Phase 5)

- [x] `deploy/kayak-env.example` exists; `/etc/kayak/env` installed
      mode 0644 root:root carrying `KAYAK_HOME=/home/pat`.
- [x] `deploy/install-secrets.sh` renamed to `install-config.sh`;
      `deploy/SETUP.md` references updated; the script installs
      `/etc/kayak/env` before secrets.
- [x] `scripts/check-config-drift.sh` manifest includes
      `deploy/kayak-env.example<TAB>/etc/kayak/env`.
- [x] Every `kayak-*.service` carries `Environment=KAYAK_HOME=/home/pat`
      + `EnvironmentFile=-/etc/kayak/env`; ExecStart arguments use
      `${KAYAK_HOME}/...`; a leading comment documents the
      WorkingDirectory/EnvironmentFile/ReadWritePaths literal-path
      reason. (ExecStart **binary path** stays literal — systemd
      257 rejects `${KAYAK_HOME}/.venv/bin/levels`: "the first
      argument may not be a variable" per systemd.exec(5). Constraint
      surfaced during Phase 5.3 and documented in the unit-file
      comment block.)
- [x] Every targeted shell script sources `/etc/kayak/env` with the
      `: "${KAYAK_HOME:=/home/pat}"` default ahead of the source.
- [x] `php/csp-report.php` reads `csp_log_path` via
      `Config::str(...)` (Phase 5.5 / T3.3 Phase 2.2 update).
- [x] `conf/snippets/levels-common.conf` carries the comment block
      explaining the nginx literal-path rationale.
- [x] Surface grep — **predicted 52, actual 86.** See the "Actual
      residual" reconciliation table in Phase 5.7 for the per-row
      breakdown. The 34-hit divergence is dominated by 13 retained
      `ExecStart=` binary-path literals (systemd constraint above),
      3 services added post-plan (cert-expiry, cert-renewal-test,
      config-drift), 11 shell-script `KAYAK_HOME:=/home/pat`
      indirection-floor defaults (these ARE the parameterization),
      and 5 PHP docstring + fallback-default refs.
- [x] `deploy/kayak-fpm-pool.conf` open_basedir directive carries
      the leading comment from Phase 5.6(b). (Phase 5.6 also added
      `/etc/kayak/runtime-config.json` to the open_basedir colon
      list itself — a latent bug surfaced after Phase 4's strict
      Config: every PHP page 500'd until the path was allowlisted.)
- [x] `deploy/SETUP.md` references `install-config.sh` (not the
      old `install-secrets.sh` name) everywhere.

### T3.5 (Phase 6)

- [x] `PLAN_pre_release_followup.md` § T3.5 table rewritten with
      "DROPPED in 0022" / "KEEP" + rationale per row; marked
      "Status: Closed (2026-05-15)". (Date is 2026-05-15, not the
      2026-05-14 originally drafted — landing slipped one day.)
- [x] `PLAN_outstanding_followups.md` § Phase 4.2 row reads
      "(closed)" with a pointer to this plan + migration 0022.
- [x] `docs/operations.md` § Schema decisions exists; summarizes
      the audit-vs-reality split; documents the VARCHAR-length
      gate; includes the table-rebuild recipe for any future
      drop.

## Effort tally

| Phase | Effort |
|---|---|
| 0 — pydantic-settings introduction | 3 h |
| 1 — `levels emit-config` + `show-config` | 4 h |
| 2 — PHP read path (incl. 2.0 $_SERVER audit) | 9 h |
| 3 — Validation hardening | 3 h |
| 4 — Dual-read removal + FPM-pool cleanup | 3 h |
| **T3.3 subtotal** | **22 h ≈ 2.75 d** |
| 5 — KAYAK_HOME indirection (T3.4) | 6 h (incl. 0.5 h that ships during T3.3 P2.2) |
| 6 — Dormant-schema decision (T3.5) | 0.5 h |
| **Grand total** | **28.5 h ≈ 3.5 d** (0.5 h overlap between T3.3 and T3.4 doesn't change the wall-clock estimate) |

Original audit estimates were T3.3=2d, T3.4=1d, T3.5=1d → 4d total.
This plan lands T3.3 in 2.75d (schema-parity test, nginx cleanup,
$_SERVER audit, FPM-pool prune added during iter 1–3), T3.4 in
0.75d (the systemd directive limits reduce the reach but still
yield a 65% literal reduction), and T3.5 in 0.06d (the substantive
work shipped in migration 0022; this is documentation only).

## Verification (end-to-end)

After Phase 6, a fresh deploy on a clean host succeeds via:

```bash
cd /home/pat/kayak                            # repo lives at canonical KAYAK_HOME

# Run 1: install /etc/kayak/env (idempotent) + secrets.env template;
#        script exits status 2 with an edit prompt.
sudo deploy/install-config.sh || true

sudo -e /etc/kayak/secrets.env                # operator fills TURNSTILE_SECRET
# (NB: /etc/kayak/env's default KAYAK_HOME=/home/pat is correct as-is
#  for this host; no operator edit needed.)

# Run 2: env already installed; secrets validated; pool overlay +
#        systemd drop-in installed; FPM restarted; nginx reloaded.
sudo deploy/install-config.sh

scripts/deploy.sh                             # incl. emit-config (atomic JSON write to /etc/kayak/runtime-config.json)
```

The `deploy.sh` step picks up the JSON; PHP-FPM picks up the JSON at
next request (once-per-request static cache); no extra reload needed
for JSON content changes (T3.3 Phase 1.5).

Then verify each layer's state:

```bash
# Python view
levels show-config

# PHP view (request hits the typed read path)
curl -fsS https://levels.mousebrains.com/login.php > /dev/null
journalctl -u php-fpm --since '1 min ago' | grep '\[CONFIG-FALLBACK\]'
# Expected: no output (fallback never fired)

# T3.4 verification: ${KAYAK_HOME} actually expanded in a service env
systemctl show kayak-pipeline | grep -E 'Environment=|KAYAK_HOME'
# Expected: KAYAK_HOME=/home/pat present in the resolved Environment=

# T3.4 surface check: residual literal count matches Phase 5.7's table
grep -rn '/home/pat' php/ scripts/ systemd/ conf/ \
    --include='*.php' --include='*.sh' \
    --include='*.service' --include='*.timer' \
    --include='*.conf' \
  | grep -v '^[^:]*:[0-9]*:\s*#' | wc -l
# Expected: 52 (verify the breakdown matches Phase 5.7's table)

# T3.5 doc-only verification
grep -nE '\(partial\)' docs/PLAN_outstanding_followups.md
# Expected: 0 matches in the Tier 3 section
```

## Reproduce

Read-only commands a subsequent session can run to re-verify the
current state described in § Why before any phase starts:

```bash
# T3.3 inventory of config read sites
grep -rn "os.environ\|load_dotenv\|getenv" src/kayak/
grep -rn "getenv\b" php/
grep -rn "fastcgi_param" conf/

# T3.3 confirm the unset nginx vars (cargo-cult finding)
grep -rn "set \$editor_feature\|set \$turnstile_site_key\|set \$mail_from\|set \$site_url" conf/ deploy/
#   (expected: no matches)

# T3.3 secrets.env current keys
sudo cat /etc/kayak/secrets.env

# T3.4 /home/pat surface count (today, pre-T3.4)
grep -rn '/home/pat' php/ scripts/ systemd/ conf/ \
    --include='*.php' --include='*.sh' \
    --include='*.service' --include='*.timer' \
    --include='*.conf' \
  | grep -v '^[^:]*:[0-9]*:\s*#' | wc -l
#   (expected 86 today; 52 after Phase 5 + T3.3 P2.5)

# T3.4 systemd specifier reality check
man systemd.exec | grep -B1 -A3 "expand\|specifier" | head -40

# T3.5 verify migration 0022 landed
sqlite3 /home/pat/DB/kayak.db ".schema maintainer_credential"
#   (expected: empty — the table is gone)
sqlite3 /home/pat/DB/kayak.db "SELECT name FROM sqlite_master WHERE type='table' AND name='maintainer_credential';"
#   (expected: empty)
```

## Out of scope (consciously deferred)

- **DB-table config.** `fetch_url`, `calc_expression`, `gauge`,
  `gauge_threshold` rows stay where they are. They're data, not
  config.
- **Hot reload.** PHP-FPM picks up new JSON only on next request
  (the once-per-request static cache); a long-running worker would
  need a SIGHUP or pool reload, which the deploy script flags but
  doesn't perform.
- **Config history / diff dashboard.** Internal-dashboard scope
  (Phase 6.1 Tier 2.4 of production-discipline); orthogonal.
- **systemd directive env-var expansion** (T3.4 frontier). A future
  systemd release that adds `${VAR}` expansion to
  `WorkingDirectory=` / `EnvironmentFile=` / `ReadWritePaths=`
  would shrink the residual literal surface; until then, the 35
  unit-file literals (12 `WorkingDirectory` + 13 `EnvironmentFile`
  + 10 `ReadWritePaths`) stay. Not worth a build-time template /
  pre-install sed substitution: drift-detector complexity > the
  cleanup gain for our one-host setup.
- **Containerization.** T3.4's KAYAK_HOME indirection helps shell
  scripts and `ExecStart=` lines move to `/app/...` cleanly in a
  container, but the systemd unit files (with their literal
  `WorkingDirectory=/home/pat/kayak` etc.) would still need a
  build-time rewrite — containers don't typically use systemd
  anyway. T3.4 is therefore NEITHER a strict prereq NOR a complete
  enabler for a future container build. File a separate plan when
  an actual container need surfaces.
- **Schema cleanup beyond migration 0022 (T3.5 frontier).** Dropping
  `ChangeStatus.auto_applied` / `ChangeTarget.trip_report` would
  require a SQLite table-rebuild migration under `@no_transaction`
  for ~6 chars of VARCHAR width. Deliberately deferred — the
  rationale is filed in `docs/operations.md` § Schema decisions
  (Phase 6.3) so a future maintainer can revisit with full context.

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
- **T3.4 systemd `Environment=` floor masks `/etc/kayak/env`
  configuration errors.** The in-unit `Environment=KAYAK_HOME=
  /home/pat` ensures ExecStart never expands to garbage even when
  `/etc/kayak/env` is missing or malformed. Side effect: a
  silently-broken env file (typo, parse error) shows nothing in
  `systemctl show <unit> | grep KAYAK_HOME` — the in-unit default
  wins. Mitigation: the install script's idempotent re-run lands
  the canonical file every time; routine deploys run
  `systemctl show kayak-pipeline.service | grep KAYAK_HOME` as a
  smoke step.
- **T3.4 install script rename.** Renaming `install-secrets.sh` →
  `install-config.sh` could surprise an operator running the old
  name from muscle memory. Acceptable risk: the script is invoked
  rarely (fresh hosts only), `deploy/SETUP.md` is updated in the
  same commit, and a missing-file shell error is a clear failure
  mode. No compat shim — keep the rename clean.

## Iter log v2 — T3.4 + T3.5 extension

> Iter loop applied to Phases 5–6 + their cross-cutting edits to
> Why / Goal / Constraints / Decisions / End-state / Effort tally /
> Verification / Out of scope.
>
> - iter v2.0 (2026-05-14): v0 of T3.4 + T3.5 extension drafted.
> - iter v2.8 (2026-05-14): 1 finding.
>   (EE2) Verification end-to-end said "Expected: ~50" for the
>         surface grep — should be 52 per Phase 5.7's table. Fixed.
>
> Convergence: 12 → 5 → 3 → 2 → 3 → 2 → 1 → 1 finding. Stopping;
> remaining items are aesthetic.
> - iter v2.7 (2026-05-14): 1 finding.
>   (DD2) Why-T3.4 section + Reproduce section still cited the
>         pre-iter-v2.2 baseline of "71 hits" / "~25 literal".
>         Updated both to the corrected 86 baseline (52 residual /
>         39 filtered) and added the PHP-FPM `open_basedir` bullet
>         for completeness in the Why-T3.4 file-format-constraint
>         list. Reduction percentage corrected from "~65%" to "55%
>         filtered".
> - iter v2.6 (2026-05-14): 2 findings.
>   (BB2) Phase 6's "5m commit + verification grep ('partial' →
>         'closed' everywhere)" line implied (partial) appears in
>         PLAN files. It doesn't (per Y2). Rewrote the verification
>         step with the actual greps that show the closeout landed.
>   (CC2) Effort tally row "5 — KAYAK_HOME indirection (T3.4) | 6 h"
>         didn't acknowledge that 0.5h of Phase 5 work actually
>         ships during T3.3 P2.2's commits (the csp-report.php
>         migration). Annotated the row + grand-total line.
> - iter v2.5 (2026-05-14): 3 findings.
>   (Y2) Phase 6.2 instructed "flip the row from '(partial)' to
>        '(closed)'" — but PLAN_outstanding_followups.md row 4.2
>        never had `(partial)` (that text lives in TaskList task
>        #32 only). Corrected with the actual row text and a
>        suggested rewrite + parenthetical disambiguating where
>        "(partial)" actually appears.
>   (Z2) Phase 6.3 said "audit-vs-reality split for the four
>        candidates" but Phase 6.1's table lists six rows. The 4
>        come from the audit's "REMOVE candidates" subset; the
>        other 2 (rating/rating_data, ChangeRequestAttachment)
>        carry audit-acknowledged KEEP justifications.
>        Disambiguated in 6.3.
>   (AA2) Phase 5 "Depends on" implied a hard cross-plan
>         dependency. Only 5.5 has a real T3.3 dependency, and that
>         work actually lands during T3.3's commits anyway. The
>         rest (5.1/5.2/5.3/5.4/5.6/5.7) is independent. Rewrote.
> - iter v2.4 (2026-05-14): 2 findings.
>   (W2) Phase 5.7's "if Phase 5 lands before T3.3 P2.5" arithmetic
>        was wrong — said "residual is 49 (38 + 11)" when filtered
>        count is 39 (+ 11 = 50) and unfiltered is 52 (+ 11 = 63).
>        Corrected.
>   (X2) Phase 5.6(a)'s comment block hardcoded
>        `PLAN_tier3_closeout.md` as a path. When the plan moves to
>        `docs/done/` the path rots. Softened to just "T3.4" (the
>        audit ID is stable; the file path is not).
> - iter v2.3 (2026-05-14): 3 findings.
>   (R2) Phase 5 "Goal" line still said "from 71 hits to ~25" —
>        stale numbers pre-dating iter v2.2's baseline correction.
>        Updated to "86 → 52 (39 filtered)".
>   (S2) Out of scope's "systemd directive frontier" bullet said
>        "the ~25 unit-file literals stay" — actual count is 35
>        (12 + 13 + 10). Updated with the directive breakdown so
>        the cited number matches the Phase 5.7 table.
>   (T2) Phase 6.1 example body had "Status: Closed (2026-05-14)"
>        hardcoded. That's the plan-drafting date, not when 6.1's
>        edits actually land. Replaced with a `(DATE)` placeholder
>        + instruction.
> - iter v2.2 (2026-05-14): 5 findings.
>   (M2) Baseline `/home/pat` grep missed `deploy/`. Re-running with
>        `deploy/` + `--include='*.example'` raises the baseline from
>        85 to 86 — one new hit in `deploy/kayak-fpm-pool.conf:38`
>        (`php_admin_value[open_basedir]` colon-list). PHP-FPM pool
>        configs do NOT expand env vars in directive values, so the
>        open_basedir line stays literal — Phase 5.6 expanded to
>        cover both nginx AND FPM pool open_basedir as documented
>        literal-path surfaces.
>   (N2) Phase 5.7 residual table updated: 51 → 52 (the new
>        open_basedir line). Filtered count: 38 → 39. Reduction
>        percentage: 55% (was 55%, rounds the same).
>   (O2) End-state checklist row added: open_basedir line carries
>        the leading comment from Phase 5.6(b).
>   (P2) Verification end-to-end's install dance was ambiguous —
>        which file does the operator edit, what order. Rewrote with
>        explicit "Run 1 / Run 2" blocks + a parenthetical noting
>        that `/etc/kayak/env`'s default KAYAK_HOME is correct as-is
>        and doesn't need editing.
>   (Q2) Verification end-to-end was missing the "PHP-FPM picks up
>        JSON at next request" note from T3.3 Phase 1.5; added it
>        so the reader knows no FPM reload is needed after
>        `deploy.sh` re-emits the JSON.
> - iter v2.1 (2026-05-14): 12 findings.
>   (A2) Phase 5.3's "Mirror in `systemd/install.service.sh` if any
>        unit's set of `EnvironmentFile=` paths changes" is moot
>        — `cmp -s` already triggers reload on any unit-file content
>        change. Replaced with a positive note that no change is
>        needed.
>   (B2) `/etc/kayak/env` template comment claimed `daemon-reload` +
>        `restart 'kayak-*.timer'` after edits. Both wrong:
>        `daemon-reload` is only needed for unit-file changes (not
>        env file contents), and timers don't read env files —
>        Type=oneshot services pick up new env at next firing.
>        Rewrote the comment.
>   (C2) Out of scope's containerization bullet originally claimed
>        T3.4 is a prereq for container builds. False: a container
>        with `KAYAK_HOME=/app` would still break on the literal
>        `WorkingDirectory=/home/pat/kayak` in unit files. T3.4 is
>        neither prereq nor complete enabler — only a partial help.
>        Reworded.
>   (D2) Phase 5.5 forward-referenced T3.3 Phase 2.2 with "edit in
>        the same commit" language that didn't make sense given
>        Phase 5 lands AFTER T3.3 ships. Restructured: T3.3
>        Phase 2.2's per-file list directly includes
>        `php/csp-report.php` (added inline above); Phase 5.5
>        documents the cross-reference but doesn't re-do the work.
>   (E2) Scope sentence at top conflated T3.4 the env-indirection
>        (this plan's Phase 5) with T3.4's deploy.sh consumer (in
>        `PLAN_outstanding_followups.md` Phase 2). Disambiguated.
>   (F2) Phase 5.7 verification counts were guesses. Re-grepped:
>        baseline today is 85 hits (not 71); post-Phase-5 residual
>        is 51 (not ~50). Updated the table with file:directive
>        breakdown including the 11 `fastcgi_param SQLITE_PATH`
>        lines that drop independently via T3.3 Phase 2.5.
>   (G2) Risks paragraph proposed a one-release shim
>        `install-secrets.sh → exec install-config.sh "$@"`. Cut.
>        Operator script invoked rarely, missing-file error is
>        clear, `deploy/SETUP.md` updates with the rename.
>   (H2) Decisions bullet on `Environment=KAYAK_HOME=` floor was
>        thin. Expanded with the `systemd.exec(5)` precedence rule
>        explicitly cited (`EnvironmentFile=` overrides
>        `Environment=` for duplicate keys).
>   (I2) End-state checklist row "Surface grep returns ~50 hits"
>        was vague. Replaced with the exact 51-hit breakdown from
>        Phase 5.7; added a row for `deploy/SETUP.md` rename
>        sweep.
>   (J2) T3.3 Phase 1's "KAYAK_HOME not landed: hardcode
>        /etc/kayak/runtime-config.json" risk paragraph was
>        written before T3.4 was in this plan. Reworded: the JSON
>        path stays /etc/kayak/runtime-config.json regardless of
>        Phase 5; `KAYAK_CONFIG_PATH` env override exists for tests
>        only, not future T3.4 swap.
>   (K2) Scope-section pointer to `PLAN_outstanding_followups.md`
>        Phase 2 forgot to mention T3.1/T3.2 already shipped.
>        Added a parenthetical so the reader doesn't wonder where
>        those went.
>   (L2) Phase 5.5 "Update T3.3 Phase 2.2's migration list" tried
>        to instruct in the same plan body that T3.3 Phase 2.2
>        should grow csp-report.php. Confusing meta-edit; cleaner:
>        directly edit T3.3 Phase 2.2 to include it, then have
>        Phase 5.5 just document the cross-reference. Done in this
>        iter.

---

End of v0 draft. Iterations follow.
