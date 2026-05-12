<?php

declare(strict_types=1);

/**
 * php-cs-fixer config for the kayak PHP layer.
 *
 * The rule set is intentionally smaller than @PSR12. The existing codebase
 * uses K&R braces ("function f(): T {" on one line), one-line if-returns
 * ("if (x) return y;"), and no blank line after the opening "<?php" tag.
 * @PSR12 would rewrite all three — a big churn diff with no behavior gain.
 * Per docs/PLAN_php_layer_split.md Phase 1.2 the goal is "PSR-12 + a
 * ruleset that matches the existing code style", so this file picks the
 * mechanical-hygiene rules and skips the brace/control-structure shape.
 *
 * Run: vendor/bin/php-cs-fixer fix
 * Check (CI gate): vendor/bin/php-cs-fixer fix --dry-run --diff
 */

$finder = (new PhpCsFixer\Finder())
    ->in(__DIR__ . '/php')
    ->name('*.php');

return (new PhpCsFixer\Config())
    ->setRiskyAllowed(true)
    ->setRules([
        // Mechanical hygiene only — no shape changes.
        'declare_strict_types' => true,           // every file already has this
        'no_unused_imports' => true,              // drop dead `use` statements
        'ordered_imports' => ['sort_algorithm' => 'alpha'],
        'array_syntax' => ['syntax' => 'short'],  // [] not array()
        'no_trailing_whitespace' => true,
        'no_trailing_whitespace_in_comment' => true,
        'no_whitespace_in_blank_line' => true,
        'single_blank_line_at_eof' => true,
        'no_extra_blank_lines' => ['tokens' => ['extra']],
        'no_singleline_whitespace_before_semicolons' => true,
        'no_whitespace_before_comma_in_array' => true,
        'whitespace_after_comma_in_array' => true,
        'space_after_semicolon' => true,
        'trim_array_spaces' => true,
        // Encoding / line endings — defensive against accidental drift.
        'encoding' => true,                       // UTF-8 BOM not used
        'line_ending' => true,                    // \n, not \r\n
        // Explicit NO on style-shape rules so a future preset bump doesn't
        // sneak them in:
        //   braces_position           — keep K&R
        //   control_structure_braces  — keep one-line if-returns
        //   blank_line_after_opening_tag — keep no-blank-after-<?php
    ])
    ->setFinder($finder)
    ->setIndent('    ')
    ->setLineEnding("\n");
