<?php
declare(strict_types=1);
/**
 * Shared HTML header with inlined CSS.
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

function get_inline_css(): string {
    static $css = null;
    if ($css === null) {
        $path = __DIR__ . '/../style.css';
        $css = file_exists($path) ? file_get_contents($path) : '';
    }
    return $css;
}

/** Build the Comment link target given a context struct and the request URI. */
function _comment_href(array $context): string {
    $type = $context['type'] ?? null;
    $id   = isset($context['id']) ? (int)$context['id'] : 0;
    if ($type === 'reach'  && $id > 0) return "/propose.php?type=reach&id=$id";
    if ($type === 'gauge'  && $id > 0) return "/propose.php?type=gauge&id=$id";
    if ($type === 'source' && $id > 0) return "/propose.php?type=source&id=$id";
    // Fallback: sniff REQUEST_URI so pages that haven't been updated still route sensibly.
    $uri = (string)($_SERVER['REQUEST_URI'] ?? '');
    if (preg_match('#^/description\.php.*?[?&]id=(\d+)#', $uri, $m)) return "/propose.php?type=reach&id={$m[1]}";
    if (preg_match('#^/gauge\.php.*?[?&]id=(\d+)#',       $uri, $m)) return "/propose.php?type=gauge&id={$m[1]}";
    if (preg_match('#^/source\.php.*?[?&]id=(\d+)#',      $uri, $m)) return "/propose.php?type=source&id={$m[1]}";
    return '/comment.php';
}

/** Render the nav bar as an HTML string. */
function render_nav(string $active, array $context): string {
    $picker_cls   = $active === 'picker'   ? ' class="active"' : '';
    $map_cls      = $active === 'map'      ? ' class="active"' : '';
    $comment_cls  = $active === 'comment'  ? ' class="active"' : '';

    $feature = editor_feature_enabled();
    $ed      = $feature ? current_editor() : null;
    $maint   = $feature && is_maintainer($ed);

    $left = '<nav class="site-nav" aria-label="Site navigation">'
          . '<a href="/picker.php"' . $picker_cls . '>Picker</a>'
          . '<a href="/map.html"' . $map_cls . '>Map</a>';

    if ($feature) {
        if ($maint && ($context['type'] ?? null) === 'reach' && !empty($context['id'])) {
            $left .= '<a href="/edit.php?id=' . (int)$context['id'] . '"' . $comment_cls . '>Edit</a>';
        } else {
            $left .= '<a href="' . htmlspecialchars(_comment_href($context)) . '"' . $comment_cls . '>Comment</a>';
        }
    }
    $left .= '</nav>';

    $right = '<nav class="site-nav-right" aria-label="Account and external">';
    if ($feature) {
        if ($ed) {
            $label = htmlspecialchars($ed['display_name'] ?: $ed['email']);
            $right .= '<span class="site-nav-id" title="' . htmlspecialchars((string)$ed['email']) . '">'
                    . $label . '</span>';
            if ($maint) {
                $right .= '<a href="/admin.php">Admin</a>';
            } else {
                $right .= '<a href="/account.php">Account</a>';
            }
            $right .= '<a href="/logout.php">Log out</a>';
        } else {
            $right .= '<a href="/login.php">Login</a>';
        }
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
    $css = get_inline_css();
    $esc_title = htmlspecialchars($title);
    $esc_desc = $description
        ? htmlspecialchars($description)
        : 'Real-time river levels, flow, and gage data from USGS, NOAA, USACE, and other government agencies.';
    $nav = render_nav($active, $context);
    echo <<<HTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$esc_title</title>
<meta name="description" content="$esc_desc">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#1b5591">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
$extra_head
<style>
$css
</style>
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
