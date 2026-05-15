# PHP conventions (`php/includes/`)

Conventions for code under `php/` and `php/includes/`. Extracted from
`CLAUDE.md` so first-time human contributors find these without
needing Claude context.

For runtime constraints (mbstring, CSP), tooling commands (composer,
PHPStan, PHPUnit, php-cs-fixer), and the integration-test scaffold,
see `CLAUDE.md` § "PHP Tooling".

## File shape

- **One concern per file.** Entry-point shims in `php/*.php` stay
  <60 lines (arg-parse + auth-gate + dispatch); the work lives in
  `php/includes/<entry>_handler.php`.
- **No load-time side effects in `includes/*.php`.** `require_once`,
  `const`, and `function` definitions only — load-time PDO calls or
  `echo` break `phpunit.xml`'s coverage isolation.

## Naming

- **File-private helpers carry the file's prefix.** Use `_<file>_*`
  (e.g. `_review_handle_post`, `_render_custom_table`,
  `_gp_fetch_series`), not bare `_*`. PHP's global function namespace
  makes bare-underscore collisions invisible locally but fatal on
  PHPStan's file-load-order in CI (see commit `998976d` for the
  lesson that motivated this).

  Enforced by `scripts/check-php-helper-prefix.sh` (pre-commit hook
  per PLAN_pre_release_followup.md § T2.8). The check accepts any
  helper whose name contains the file's basename stem (with
  `_handler` / `_detail` suffixes stripped), plus the documented
  `_gp_*` cross-file cluster used by the gauge_plots* family. 24
  grandfathered helpers from before the check landed live in
  `scripts/php-helper-prefix.allowlist`; new helpers must satisfy
  the rule rather than appending there.
- **Module constants follow the same boundary.** Use `<FILE>_<NAME>`
  (e.g. `CUSTOM_LEVELS_STATUS_META`, `REVIEW_LIST_STATUSES`).
- **Public vs private.** Function names without a leading underscore
  are part of the file's public API (consumed by other files); names
  with a leading underscore are file-private and may be renamed
  freely.
