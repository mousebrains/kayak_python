<?php
declare(strict_types=1);
/**
 * Provider-attribution links.
 *
 * Most sources are government agencies whose per-station page URL is derived
 * from a station id (USGS waterdata, NWRFC flowplot, …) — see the per-agency
 * maps in source.php / description_detail.php. A few providers are local
 * operators with only a homepage and no per-station page, so they get a fixed
 * attribution link keyed on the source's `agency` string instead.
 *
 * Centralized here so the agency string and its URL can't drift between the
 * source page and the description page, and so adding another such provider is
 * a single edit. New entries belong in this one function.
 */

/**
 * Return the attribution homepage for a source's agency, or null when the
 * agency has no special-cased provider link.
 *
 * Matching is case-insensitive and tolerates a trailing location suffix, so
 * the stored "Cowlitz County Fire District 5, Kalama" still matches. The
 * negative lookahead on a trailing digit keeps a hypothetical future sibling
 * like "...Fire District 50" from accidentally inheriting FD5's link (the
 * value carries a ", Kalama" suffix, so the end can't simply be anchored).
 * PCRE without the /u modifier is ASCII-only — no mbstring dependency (prod
 * PHP-FPM lacks mbstring).
 */
function agency_attribution_url(?string $agency): ?string {
    if ($agency === null || $agency === '') {
        return null;
    }
    if (preg_match('/Cowlitz County Fire District 5(?![0-9])/i', $agency) === 1) {
        return 'https://www.cowlitzfd5.org';
    }
    return null;
}
