# Plan — PHPStan: clear the baseline, then level 9 + strict-rules

**Status:** Complete (branch `phpstan-strict`; Stages 1–4 landed 2026-05-24/25).
Final bar: **level 9 + full `phpstan-strict-rules`** (no toggles), zero live
findings, with a shrinking 634-entry level-9 `mixed` baseline.

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
| level 9 + strict-rules (full)  | +324 over Stage 3 | **Stage 4** — fixed all 324; baseline 640→634 |

The level-9 jump is the classic web-app explosion: every `$_GET` / `$_POST` /
`json_decode` / PDO fetch result is `mixed`, and level 9 flags each use
(cast.string/double/int 324, argument.type 121, offsetAccess 99, …).

**Tuned strict-rules** (Stages 2–3) = full strict-rules minus the two
highest-churn families: `booleansInConditions` (~237 finds — `if ($row !== false)`
over the idiomatic `if ($row)`) and `disallowedShortTernary` (~84 finds — bans
`$x ?: $y`). **Stage 4 (2026-05-25) re-enabled both** and fixed all 324 resulting
finds to zero (behaviour-preserving), so there are no `strictRules` toggles left —
full strict-rules is enforced.

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

### Stage 4 — enable the two stylistic families (fix to zero)
Maintainer opted (2026-05-25) to go after the families Stages 2–3 had deferred.
Removed the `parameters.strictRules` toggles so `booleansInConditions` +
`disallowedShortTernary` enforce, then fixed all **324** resulting finds to zero,
behaviour-preserving, by the type→comparison table below. The level-9 `mixed`
finds stay baselined; six were incidentally eliminated by narrowing fixes, so the
baseline shrank 640 → **634**.

Type→fix table used (the condition/operand `$x` must become boolean):
- `string` → `$x !== ''` / `=== ''`; `string|null` → `($x ?? '') !== ''` (or
  `$x !== null && $x !== ''` when `$x` must stay **narrowed** for a later
  string call — `($x ?? '')` does not narrow `$x`).
- array/list → `$x !== []` / `=== []`; `array|null` → `($x ?? []) !== []`;
  `$stmt->fetch()` row (`array|false`) → `$x !== false` / `=== false`.
- `int` → `$x !== 0`; `strpos()` → `!== false` (0 is valid); `preg_match()` →
  `=== 1`; `strtotime`/`filter_var`/`filemtime` (`int|false`) → `!== false`.
- `filter_input(…, FILTER_VALIDATE_INT)` (`int|false|null`): id → `is_int($x) &&
  $x >= 1`; flag → `is_int($x) && $x !== 0`.
- short `$a ?: $b` → `??` when null is the only falsy case; else a long ternary
  with a boolean condition; a function-call `$a` is hoisted to a temp first (no
  double-eval).
- genuinely-truthy `mixed` flags/counters (`no_show`, `significant`, Turnstile
  `success`, `HTTPS`, numeric `optimal_flow`/`basin_area`) → `(bool)$x`, the only
  exact behaviour-preserving form; nullable `mixed` columns → `!== null`.

**Exit:** `composer analyse` (level 9 + **full** strict-rules, with the 634
baseline) → 0 errors; no `strictRules` toggles remain.
**✓ Done** — 53 files, behaviour-preserving; `composer analyse` → 0,
`composer fix-check` clean, 172 phpunit tests pass.

## Shrinking the baseline (follow-up work)

The 640 are almost all rooted in `array<string,mixed>` PDO rows: `db_rows()` /
`db_row()` / `fetch()` lose the column shape, so every `$row['col']` is `mixed`
and every cast / offset / concat / typed-call on it is flagged. The high-ROW
reducer is to give the hot queries real row shapes (PHPStan `@return
list<array{...}>` on the query wrappers, or per-call `/** @var */`), starting
with the worst files (`gauge_detail`, `review_handler`, `description_detail`,
`reach_detail` ≈ 270 of 640). Each reduction: tighten types → `composer
baseline` → commit the smaller file.

**Progress (2026-05-25):** first reduction pass — typed every DB-query row in
`gauge_detail.php` (79 → 9) and `review_handler.php` (74 → 20) with verified
`array{…}` shapes (PDO returns `int`/`float`/`string` for INTEGER/REAL+NUMERIC/
TEXT, `int` for BOOLEAN; runtime-probed against the dev DB), removing the
now-redundant casts and null-guarding where a nullable column feeds a non-null
call. Baseline **640 → 510**. Residuals in those two files are genuinely-dynamic
`mixed` casts (`$_POST`/`$_GET` superglobals, `json_decode` payload values,
external regression-artifact JSON) — left as-is, not guessed.

Second pass typed `description_detail.php` (59 → 0), `reach_detail.php` (54 → 0),
`source.php` (37 → 0), and `propose_handler.php` (40 → 12; rest is superglobal /
JSON). Baseline **510 → 332**. Next worst: `review_logic.php` (35),
`svg_plot.php` (28), `reach_search.php` (25), `custom_handler.php` (21),
`review_handler.php` (20).

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
