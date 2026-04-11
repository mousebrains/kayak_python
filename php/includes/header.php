<?php
/**
 * Shared HTML header with inlined CSS.
 *
 * Usage: include_header('Page Title', $active_nav)
 */

function get_inline_css(): string {
    static $css = null;
    if ($css === null) {
        $path = __DIR__ . '/../style.css';
        $css = file_exists($path) ? file_get_contents($path) : '';
    }
    return $css;
}

function include_header(string $title = 'River Levels', string $active = ''): void {
    header('X-Content-Type-Options: nosniff');
    header('X-Frame-Options: SAMEORIGIN');
    $css = get_inline_css();
    $esc_title = htmlspecialchars($title);
    $picker_cls = $active === 'picker' ? ' class="active"' : '';
    echo <<<HTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$esc_title</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#2060A0">
<link rel="icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<style>
$css
</style>
</head>
<body>
<a href="#main" class="skip-link">Skip to main content</a>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="Site navigation"><a href="/picker.php"$picker_cls>Picker</a> <a href="/map.html">Map</a></nav>
</header>
<main id="main">
HTML;
}
