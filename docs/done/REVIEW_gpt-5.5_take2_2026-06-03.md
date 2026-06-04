# GPT-5.5 Follow-up Review - 2026-06-03

Scope: reviewed commit `39e7b6c` (`fix: address 2026-06-03 gpt-5.5 project-review findings`) against `origin/main` / `4f27d0c`, with the previous review notes in `gpt-5.5.md` as context.

User clarification during this pass: the 14-day per-source dead-feed window is an intentional operator decision. I treated that as accepted policy, not as a finding.

## Findings

### MEDIUM - Runtime config refresh still cannot carry the root-only Turnstile secret, so deploy can silently disable captcha

The follow-up correctly replaces the stale runbook command with the secure wrapper pipeline, but that path still has a credential propagation hole: the JSON is rendered by `pat`, while the production Turnstile secret is documented as root-only.

Relevant current paths:

- `scripts/deploy.sh:203-214` runs `levels emit-config --dry-run` unprivileged as `pat`, then pipes the JSON to the root-owned installer wrapper.
- `deploy/SETUP.md:288-305` says `TURNSTILE_SECRET` lives in `/etc/kayak/secrets.env`, mode `0600 root:www-data`, edited with `sudo -e`.
- `src/kayak/config.py:70-72` only loads `/etc/kayak/secrets.env` if the current process can read it. A normal `pat` deploy cannot read a `0600 root:www-data` file.
- `php/includes/config.php:13-17` and `php/includes/config.php:122-125` make the JSON the source of truth and removed the getenv fallback.
- `php/includes/turnstile.php:18-23` enables Turnstile only when both JSON keys are present; `php/includes/turnstile.php:45-46` returns `true` when disabled.

So after the documented deploy path runs, `turnstile_secret` is omitted from `/etc/kayak/runtime-config.json` unless the operator duplicates that secret into `pat`'s environment. The PHP-FPM `env[TURNSTILE_SECRET]` channel described in `deploy/SETUP.md:315-318` does not help because `Config::str('turnstile_secret')` no longer consults `getenv()`. Net effect: login/contact anti-spam silently turns off even though `/etc/kayak/secrets.env` is populated.

Fix options:

- Preferably keep the no-`pat`-code-as-root boundary and teach `/usr/local/sbin/kayak-install-runtime-config` to merge root-readable `/etc/kayak/secrets.env` values into the piped JSON before installing it.
- Alternatively, make `turnstile_secret()` fall back only to `getenv('TURNSTILE_SECRET')`, matching the existing FPM secret channel, and update the comments/tests to make that single exception explicit.

Add a regression test for the production shape: `pat`-rendered JSON lacks `turnstile_secret`, `/etc/kayak/secrets.env` or FPM env contains it, and `turnstile_enabled()` still ends up true.

## Follow-up Checks

The core fixes from the prior review otherwise look sound:

- Health check now performs a per-source query in addition to the global max timestamp. With the accepted `STALE_SOURCE_DAYS=14` policy, the implementation and tests match the updated `docs/slo.md`.
- The review reply race is fixed: `review_send_reply()` now updates only `WHERE id = ? AND status = 'pending'`, returns `false` on a lost race, and sends no misleading email.
- `validate-config --known-env --strict` now scans `METADATA_` and adds the needed known names for `KAYAK_DATA`, `KAYAK_VENV`, `USGS_API_KEY`, `HC_FETCH_OSMB`, and `HC_STATUS`.
- The local PHP quick-start docs now include `levels emit-config --out ...` and `KAYAK_CONFIG_PATH=...`.
- The stale active `_internal` test-host and dropped `pages` references called out in the previous review were cleaned in the operational docs touched by this commit.

Minor residual doc note, not promoted to a finding: older plan/history files still mention the old `≤2h/source` freshness target (`docs/PLAN_production_discipline.md:257`, `docs/done/PLAN_outstanding_followups.md:755-758`). Since `docs/slo.md` is now the canonical target and the 14-day window was a deliberate decision, this is only a cleanup opportunity if those planning docs are still used as operator-facing references.

## Verification

Commands run:

```text
pytest tests/test_scripts/test_health_check.py tests/test_cli/test_validate_config.py
vendor/bin/phpunit tests/php/ReviewLogicFunctionalTest.php
git diff --check origin/main..HEAD
```

Results:

```text
26 passed
OK (21 tests, 145 assertions)
git diff --check: clean
```

Live/local DB spot-check against `../DB/kayak.db`:

- The new health-check monitor scope currently covers 303 gauge-linked active/OGC USGS sources.
- With `STALE_SOURCE_DAYS=14`, the new stale-source SQL returned no failing rows in the local live DB snapshot.
