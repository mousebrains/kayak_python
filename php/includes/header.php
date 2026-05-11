<?php
declare(strict_types=1);
/**
 * Shared HTML header linking to the content-hashed external stylesheet
 * written by `levels build` at /static/style-<hash>.css. Falls back to
 * inline CSS if the hash sidecar is missing (e.g. dev setups before a
 * build has run).
 *
 * Usage:
 *   include_header('Title');                              // minimal
 *   include_header('Title', 'picker');                    // mark nav active
 *   include_header('Title', '', $desc, $extra_head,       // full form
 *                  ['type' => 'reach', 'id' => 42]);
 *
 * The optional $context array tells the nav bar where a "Comment" click
 * should land. Recognized keys: type ('reach'|'gauge'|'source'|'site'),
 * id (int, optional).
 */

require_once __DIR__ . '/auth.php';

/**
 * Return the <link> / <style> block to embed in <head>. Prefers the hashed
 * external stylesheet (written by build); falls back to inline CSS if the
 * sidecar is missing.
 */
function css_head_block(): string {
    static $block = null;
    if ($block !== null) return $block;
    $doc_root = __DIR__ . '/..';
    $hash_path = $doc_root . '/static/style.css.hash';
    if (is_readable($hash_path)) {
        $hash = trim((string)file_get_contents($hash_path));
        if ($hash !== '' && is_readable("$doc_root/static/style-$hash.css")) {
            $block = '<link rel="stylesheet" href="/static/style-'
                   . htmlspecialchars($hash) . '.css">';
            return $block;
        }
    }
    // Fallback: inline whatever style.css exists so the page still renders.
    $path = $doc_root . '/style.css';
    $css = is_readable($path) ? (string)file_get_contents($path) : '';
    $block = "<style>\n$css\n</style>";
    return $block;
}

/** Render the nav bar as an HTML string.
 *
 * The single "Picker" link points at /picker.php on reach-context pages and
 * /gauge_picker.php on gauge-context pages. Choice is driven by
 * $context['type'] (already used for the Comment redirect): 'gauge' /
 * 'source' → gauge picker, everything else → reach picker. Pages that mean
 * to override (e.g. picker.php and gauge_picker.php themselves) can pass
 * 'picker_kind' explicitly.
 */
function render_nav(string $active, array $context): string {
    $picker_cls  = $active === 'picker'  ? ' class="active"' : '';
    $map_cls     = $active === 'map'     ? ' class="active"' : '';
    $comment_cls = $active === 'comment' ? ' class="active"' : '';

    $kind = $context['picker_kind'] ?? null;
    if ($kind === null) {
        $type = $context['type'] ?? '';
        $kind = ($type === 'gauge' || $type === 'source') ? 'gauge' : 'reach';
    }
    if ($kind === 'gauge') {
        $picker_url   = '/gauge_picker.php';
        $picker_label = 'Gauge<br>Picker';
    } else {
        $picker_url   = '/picker.php';
        $picker_label = 'Reach<br>Picker';
    }

    $feature = editor_feature_enabled();
    $ed      = $feature ? current_editor() : null;
    $maint   = $feature && is_maintainer($ed);

    $left = '<nav class="site-nav" aria-label="Site navigation">'
          . '<a href="' . $picker_url . '"' . $picker_cls . '>' . $picker_label . '</a>'
          . '<a href="/map.html"' . $map_cls . '>Map</a>';

    // Maintainers still get a prominent Edit shortcut on reach pages.
    // Everyone else reaches the Comment form through the footer.
    if ($feature && $maint && ($context['type'] ?? null) === 'reach' && !empty($context['id'])) {
        $left .= '<a href="/edit.php?id=' . (int)$context['id'] . '"' . $comment_cls . '>Edit</a>';
    }
    $left .= '</nav>';

    $right = '<nav class="site-nav-right" aria-label="Account and external">';
    if ($feature && $ed) {
        $label = htmlspecialchars($ed['display_name'] ?: $ed['email']);
        $right .= '<span class="site-nav-id" title="' . htmlspecialchars((string)$ed['email']) . '">'
                . $label . '</span>';
    }
    $right .= '<a href="https://wkcc.org" rel="noopener" target="_blank">WKCC</a>';
    $right .= '</nav>';

    return $left . $right;
}

function include_header(
    string $title = 'River Levels',
    string $active = '',
    string $description = '',
    string $extra_head = '',
    array $context = []
): void {
    $css_block = css_head_block();
    $esc_title = htmlspecialchars($title);
    $esc_desc = $description
        ? htmlspecialchars($description)
        : 'Real-time river levels, flow, and gage data from USGS, NOAA, USACE, and other government agencies.';
    $nav = render_nav($active, $context);
    $scheme = ($_SERVER['HTTPS'] ?? '') ? 'https' : 'http';
    $host   = (string)($_SERVER['HTTP_HOST'] ?? 'levels.wkcc.org');
    $path   = strtok((string)($_SERVER['REQUEST_URI'] ?? '/'), '?');
    $url    = htmlspecialchars($scheme . '://' . $host . $path);
    echo <<<HTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$esc_title</title>
<meta name="description" content="$esc_desc">
<link rel="canonical" href="$url">
<meta property="og:type" content="website">
<meta property="og:site_name" content="WKCC River Levels">
<meta property="og:title" content="$esc_title">
<meta property="og:description" content="$esc_desc">
<meta property="og:url" content="$url">
<meta property="og:image" content="$scheme://$host/static/icon-192.png">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="$esc_title">
<meta name="twitter:description" content="$esc_desc">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#1b5591" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0d3057" media="(prefers-color-scheme: dark)">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
$extra_head
$css_block
</head>
<body>
<a href="#main" class="skip-link">Skip to main content</a>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  $nav
</header>
<main id="main">
HTML;
}
