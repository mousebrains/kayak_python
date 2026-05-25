# Plan — PHPStan: clear the baseline, then level 9 + strict-rules

**Status:** Complete (branch `phpstan-strict`; Stages 1–3 landed 2026-05-24/25).

## Context

`php/` runs PHPStan **level 8** with a grandfather list (`phpstan-baseline.neon`)
carrying pre-existing `PDOStatement|false` / `string|false` narrowing finds.
Goal: eliminate the baseline (hold the whole layer to level 8 with zero
suppressions), then raise the bar to **level 9** (`mixed` strictness) and add
**`phpstan/phpstan-strict-rules`**, fixing every finding so PHPStan is clean at
the higher bar with **no baseline**.

## Sizing (Reproduce)

A throwaway config at level N over `php/` with **no baseline** measures each
stage. `vendor/bin/phpstan analyse -c <tmp>.neon --memory-limit=1G`:

| Bar (no baseline) | Findings | Outcome |
|---|---|---|
| level 8 | 79 | **Stage 1** — fixed all at the source; baseline deleted |
| + strict-rules (full) | 431 | sized; the two stylistic families = ~321 of them |
| + strict-rules (tuned) | 110 | **Stage 2** — fixed all to zero |
| level 9 + strict-rules (tuned) | 640 | **Stage 3** — fresh shrinking baseline |

The level-9 jump is the classic web-app explosion: every `$_GET` / `$_POST` /
`json_decode` / PDO fetch result is `mixed`, and level 9 flags each use
(cast.string/double/int 324, argument.type 121, offsetAccess 99, …).

**Tuned strict-rules** = full strict-rules minus the two highest-churn,
lowest-bug-value families (maintainer call, 2026-05-25): `booleansInConditions`
(~237 finds — would force `if ($row !== false)` over the idiomatic `if ($row)`)
and `disallowedShortTernary` (~84 finds — bans `$x ?: $y`). Both are turned off
via `parameters.strictRules` in `phpstan.neon`; every other strict rule is
enforced at zero.

## Stages

### Stage 1 — eliminate the baseline (level 8 → 0 findings, no suppressions)
Fix the 79 level-8 finds at the source, then delete `phpstan-baseline.neon` and
its `includes:` entry in `phpstan.neon`. Dominant patterns + idiomatic fixes:
- **`PDOStatement|false`** (prepare/query → fetch/fetchAll/fetchColumn): PDO runs
  in `ERRMODE_EXCEPTION` (`db.php`), so these never return `false` at runtime —
  PHPStan can't infer that. Fix with a typed prepare/query helper (or a localized
  guard) so the `false` arm is gone at the type level. **One helper kills most.**
- **`string|false`** (substr/explode/date/htmlspecialchars on `string|false`):
  guard or cast at the boundary where the `false` enters.
- **`float|string` / `int|string` / mixed inputs** into typed validators: cast /
  validate the `$_GET`/`$_POST` value before the typed call.
- One-offs: `setcookie` options array shape, `json_decode string|true`,
  `min/max` non-empty-array, the float/null strict-comparison.

**Exit:** `composer analyse` (level 8, baseline removed) → 0 errors.
**✓ Done** — fixed across several commits ending 748d762; the 55-entry
`phpstan-baseline.neon` and its `includes:` line were deleted.

**Chosen approach (maintainer, 2026-05-24): incremental.** The ~636-find level-9
big-bang was rejected as too large/prod-risky for one branch. Instead: clear the
baseline (Stage 1), add strict-rules and fix to zero (Stage 2), then adopt
level 9 with a *fresh, shrinking* baseline so new code is held to level 9
without the mega-diff (Stage 3).

### Stage 2 — add `phpstan/phpstan-strict-rules` (fix to zero, at level 8)
`composer require --dev phpstan/phpstan-strict-rules`; include its `rules.neon`
in `phpstan.neon`. Fix **every** finding (strict `===`/`!==`, no loose
truthiness, no switch fallthrough, no variable variables, etc.) — higher-value
and lower-volume than the level-9 mixed explosion.

**Exit:** `composer analyse` (level 8 + strict-rules, no baseline) → 0 errors.
**✓ Done** — commits a112118 (110 behavior-preserving fixes: strict
`in_array`/`array_filter`, explicit `empty()` comparisons, redundant-cast
removal, loop-var renames, `==`→`===`, `strval()` on numeric HUC keys) and
aa707f8 (require the dep + include the tuned ruleset). 172 phpunit tests pass.

### Stage 3 — adopt level 9 with a fresh, shrinking baseline
Bump `phpstan.neon` `level: 8 → 9`. Rather than a 636-fix mega-diff, regenerate
`phpstan-baseline.neon` at level 9 (`composer baseline`) so the existing
mixed-strictness finds are grandfathered, **new code is held to level 9**, and
the baseline shrinks in follow-ups. The baseline header documents it as
level-9-adoption grandfathering (distinct from the level-8 list Stage 1 cleared).

**Exit:** `composer analyse` (level 9 + strict-rules, with the fresh baseline)
→ 0 errors; the baseline contains only pre-existing level-9 `mixed` finds.
**✓ Done** — `phpstan.neon` `level: 9`; regenerated `phpstan-baseline.neon`
(640 finds / 260 entries) with a header noting it as a *shrinking* level-9
debt list. `composer analyse` → 0.

## Shrinking the baseline (follow-up work)

The 640 are almost all rooted in `array<string,mixed>` PDO rows: `db_rows()` /
`db_row()` / `fetch()` lose the column shape, so every `$row['col']` is `mixed`
and every cast / offset / concat / typed-call on it is flagged. The high-ROW
reducer is to give the hot queries real row shapes (PHPStan `@return
list<array{...}>` on the query wrappers, or per-call `/** @var */`), starting
with the worst files (`gauge_detail`, `review_handler`, `description_detail`,
`reach_detail` ≈ 270 of 640). Each reduction: tighten types → `composer
baseline` → commit the smaller file.

## Per-stage discipline

Each stage: iterate `composer analyse` to 0 → run `composer test` (phpunit) +
`composer fix-check` (php-cs-fixer) + `vendor/bin/phpunit` + the full
`PIP_USER=0 pre-commit run --all-files` to catch regressions → commit. The PHP
behaviour must not change — these are type-safety fixes, not logic changes.

## Verification

`composer analyse` clean at each stage's bar — level 8 with **no** baseline after
Stage 1; level 8 + tuned strict-rules after Stage 2; level 9 + tuned strict-rules
with the fresh **shrinking** baseline after Stage 3. `composer test` (172 tests)
green and `composer fix-check` clean at every commit; the PHP behaviour is
unchanged (type-safety only).
