<?php
/**
 * Shared HTML header with inlined CSS.
 *
 * Usage: include_header('Page Title', $active_nav)
 */

function get_inline_css(): string {
    static $css = null;
    if ($css === null) {
        $path = dirname(__DIR__, 2) . '/src/kayak/web/static/style.css';
        $css = file_exists($path) ? file_get_contents($path) : '';
    }
    return $css;
}

function include_header(string $title = 'River Levels', string $active = ''): void {
    $css = get_inline_css();
    $esc_title = htmlspecialchars($title);
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
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav><a href="/index.html">Home</a></nav>
</header>
<main>
HTML;
}
