<?php
declare(strict_types=1);
/**
 * Request glue for the base-62 public handle (the codec lives in pubhash.php).
 *
 * The canonical public URL addresses a row by `?h=<handle>`, where
 * `handle = pubhash_encode(id)`. The legacy `?id=<decimal>` form is still
 * honored because the id is stable: HTML pages 301 it to the `?h=` canonical
 * via pubhash_redirect_legacy_id(); machine endpoints (data/plot/api/…) just
 * resolve it in place with pubhash_param_id().
 *
 * These read the live request via filter_input(INPUT_GET, …), so they are
 * exercised by the IntegrationTestCase HTTP suite (real GET requests), not the
 * in-process FunctionalTestCase (whose $_GET assignments filter_input ignores).
 */

require_once __DIR__ . '/pubhash.php';
require_once __DIR__ . '/http_exit.php';

/**
 * Resolve the addressed row id: the canonical `?h=<base62 handle>` if present,
 * else the legacy `?id=<decimal>`. Returns null when neither yields a positive
 * integer — a malformed handle, a non-positive id, or no param at all — and the
 * caller then 404s or falls back to a default (e.g. "first row").
 */
function pubhash_param_id(): ?int
{
    $h = filter_input(INPUT_GET, 'h', FILTER_DEFAULT);
    if (is_string($h) && $h !== '') {
        try {
            $n = pubhash_decode($h);
            // "0"/"00" decode to 0, which encode() never mints (ids are 1-based);
            // normalize sub-1 to null so the ?h= branch agrees with the ?id= one
            // below and no caller inherits a latent pubhash_encode(0) throw.
            return $n >= 1 ? $n : null;
        } catch (InvalidArgumentException) {
            return null; // malformed handle → treat as not-found
        }
    }
    $id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
    return (is_int($id) && $id >= 1) ? $id : null;
}

/**
 * HTML-page canonicalizer: when a row was addressed via the legacy
 * `?id=<decimal>` (and not the canonical `?h=`), 301 to the `?h=` URL,
 * preserving every other query param. No-op when `?h=` was used or no positive
 * `?id=` is present (search / default modes). Terminates the request on
 * redirect via the http_terminate() seam.
 *
 * The redirect is pre-lookup, so a bogus id (no such row) 301s to its handle
 * and then 404s at the canonical URL — keeping this helper free of any DB
 * dependency.
 */
function pubhash_redirect_legacy_id(): void
{
    $h = filter_input(INPUT_GET, 'h', FILTER_DEFAULT);
    if (is_string($h) && $h !== '') {
        return; // already canonical
    }
    $id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
    if (!is_int($id) || $id < 1) {
        return; // nothing to canonicalize (search/default mode)
    }

    $params = $_GET;
    unset($params['id']);
    $params['h'] = pubhash_encode($id);
    $script = $_SERVER['SCRIPT_NAME'] ?? '';
    $script = is_string($script) ? $script : '';

    header('Location: ' . $script . '?' . http_build_query($params));
    http_terminate(301);
}

/**
 * Build a canonical public detail URL: `/<page>.php?h=<handle>[<extra>]`.
 *
 * $id must be a positive row id (a primary key); $extra is appended verbatim
 * after the handle (e.g. "&type=gauge", "&amp;hidden=1") and must already be
 * escaped for its output context.
 */
function pubhash_url(string $page, int $id, string $extra = ''): string
{
    return '/' . $page . '.php?h=' . pubhash_encode($id) . $extra;
}

/**
 * Resolve a custom-list of row ids from the canonical `?h=<handle,handle,…>`
 * (the comma-list sibling of pubhash_param_id, used by custom.php /
 * custom_gauges.php / the pickers).
 *
 * Each comma element is a base-62 handle; malformed or non-positive elements
 * are skipped. Returns a de-duplicated list<int> in first-seen order. The
 * legacy `?ids=<decimal,…>` form is canonicalized to `?h=` by
 * pubhash_redirect_legacy_ids() before this runs, so this reads `?h=` only.
 *
 * @return list<int>
 */
function pubhash_param_ids(): array
{
    $raw = filter_input(INPUT_GET, 'h', FILTER_DEFAULT);
    if (!is_string($raw) || $raw === '') {
        return [];
    }
    $ids = [];
    $seen = [];
    foreach (explode(',', $raw) as $tok) {
        try {
            $n = pubhash_decode($tok);
        } catch (InvalidArgumentException) {
            continue; // malformed handle → skip
        }
        if ($n >= 1 && !isset($seen[$n])) {
            $seen[$n] = true;
            $ids[] = $n;
        }
    }
    return $ids;
}

/**
 * HTML-page canonicalizer for the custom-list pages: when the row list was
 * addressed via the legacy `?ids=<decimal,…>` (and not the canonical `?h=`),
 * 301 to the `?h=<handle,…>` URL, preserving every other query param. No-op
 * when `?h=` was used or no positive id is present. Terminates on redirect via
 * the http_terminate() seam.
 *
 * Pre-lookup like its single-id sibling: a bogus id encodes to a handle and is
 * dropped later by pubhash_param_ids() / the page's empty-list redirect, so
 * this stays DB-free.
 */
function pubhash_redirect_legacy_ids(): void
{
    $h = filter_input(INPUT_GET, 'h', FILTER_DEFAULT);
    if (is_string($h) && $h !== '') {
        return; // already canonical
    }
    $ids = filter_input(INPUT_GET, 'ids', FILTER_DEFAULT);
    if (!is_string($ids) || $ids === '') {
        return; // nothing to canonicalize
    }

    $handles = [];
    foreach (explode(',', $ids) as $tok) {
        $n = filter_var(trim($tok), FILTER_VALIDATE_INT);
        if (is_int($n) && $n >= 1) {
            $handles[] = pubhash_encode($n);
        }
    }
    if ($handles === []) {
        return; // no valid ids → let the page's empty-list path handle it
    }

    $other = $_GET;
    unset($other['ids'], $other['h']);
    // Literal commas (sub-delims, RFC 3986) match the ?h= form the picker JS
    // builds, so the 301 lands on the exact canonical URL.
    $query = 'h=' . implode(',', $handles);
    if ($other !== []) {
        $query .= '&' . http_build_query($other);
    }
    $script = $_SERVER['SCRIPT_NAME'] ?? '';
    $script = is_string($script) ? $script : '';

    header('Location: ' . $script . '?' . $query);
    http_terminate(301);
}
