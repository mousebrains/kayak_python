<?php
declare(strict_types=1);
/**
 * Shared Leaflet map emitter for gauge/reach detail pages.
 *
 * Emits a div that static/feature-map.js picks up after page load. The
 * caller decides which labelled points to show and whether to attach a
 * river track polyline.
 *
 * Callers MUST include the Leaflet stylesheet in <head> via gm_head_links()
 * — emitting <style> from here would land it inside <main>, which the HTML
 * spec disallows (style is metadata, not flow content). The reach-popup
 * CSS is shipped in the main style.css, so no per-page injection needed.
 */

require_once __DIR__ . '/pubhash.php';

/**
 * Return the <head> fragment a map-bearing page must include in extra_head.
 *
 * Currently just the Leaflet stylesheet link. Static, but exposed as a
 * function so future additions (preload hints, etc) have one place to land.
 */
function gm_head_links(): string
{
    return '<link rel="stylesheet" href="/static/leaflet.css">';
}

/**
 * Render a Leaflet map block with labelled markers + optional river track(s).
 *
 * Use $geom for the single non-clickable track on a reach detail page;
 * use $reach_tracks for one-or-many clickable polylines (each opens a
 * popup linking to the reach description page).
 *
 * @param array<string,string> $points       Label → "lat,lon" (e.g. 'Gauge' => '44.56,-123.25').
 * @param ?string              $geom         Decorative track "lon lat,lon lat,..." or null.
 * @param string               $track_color  CSS colour for both $geom and $reach_tracks.
 * @param array<int,array{id:int,name:string,geom:string,location?:string,classes?:string,status?:string}> $reach_tracks
 *               Optional list of clickable reach tracks (LineString geoms
 *               only — Point reaches should be skipped by the caller).
 *               location/classes/status are popup-only metadata; status
 *               drives the polyline colour ('low'|'okay'|'high'|'unknown').
 * @param ?int   $gauge_id  When set, emitted as data-gauge-id on the map
 *               div; feature-map.js routes clicks on the 'Gauge' marker
 *               to /gauge.php?id=N instead of Google Maps. Omit on pages
 *               where the gauge marker should still open Google Maps
 *               (e.g. gauge.php itself).
 * @return bool  True if a map was emitted. Caller uses this to decide whether
 *               to enqueue leaflet.js + feature-map.js at end of body.
 */
function gm_render_map(
    array $points,
    ?string $geom = null,
    string $track_color = '#2196F3',
    array $reach_tracks = [],
    ?int $gauge_id = null
): bool {
    if ($points === [] && ($geom ?? '') === '' && $reach_tracks === []) {
        return false;
    }

    $parse_geom = static function (string $s): array {
        $out = [];
        foreach (explode(',', $s) as $pair) {
            $parts = preg_split('/\s+/', trim($pair));
            if ($parts !== false && count($parts) === 2) {
                $out[] = [(float)$parts[1], (float)$parts[0]];
            }
        }
        return $out;
    };

    $track_json = 'null';
    if ($geom !== null && $geom !== '') {
        $track = $parse_geom($geom);
        if ($track !== []) {
            $track_json = (string)json_encode($track);
        }
    }

    $rt_payload = [];
    foreach ($reach_tracks as $rt) {
        $coords = $parse_geom($rt['geom']);
        if (count($coords) >= 2) {
            $rt_payload[] = [
                'id' => $rt['id'],
                'h' => pubhash_encode($rt['id']),
                'name' => $rt['name'],
                'location' => $rt['location'] ?? '',
                'classes' => $rt['classes'] ?? '',
                'status' => $rt['status'] ?? 'unknown',
                'points' => $coords,
            ];
        }
    }
    $rt_json = $rt_payload !== [] ? (string)json_encode($rt_payload) : '[]';

    $points_attr = htmlspecialchars((string)json_encode($points));
    $track_attr  = htmlspecialchars($track_json);
    $color_attr  = htmlspecialchars($track_color);
    $rt_attr     = htmlspecialchars($rt_json);

    // Map config (default extent + OSMB-style overlay layer defs) — the build
    // generates /static/site-config.json; feature-map.js fetches it and builds
    // its overlay layers + popups from it (the per-layer GeoJSON URLs live inside
    // that JSON). Same /static/<file>?v=<mtime> contract map.html uses; empty
    // string when the file isn't present (JS falls back to no overlays).
    $static_url = static function (string $name): string {
        $path = $_SERVER['DOCUMENT_ROOT'] . '/static/' . $name;
        if (!is_file($path)) {
            return '';
        }
        return '/static/' . $name . '?v=' . (string)filemtime($path);
    };
    $site_config_attr = htmlspecialchars($static_url('site-config.json'));

    $gauge_id_attr = $gauge_id !== null
        ? ' data-gauge-id="' . $gauge_id . '" data-gauge-h="' . pubhash_encode($gauge_id) . '"'
        : '';

    echo '<div id="feature-map"'
        . ' style="height:350px;margin-top:1rem;border:1px solid #ccc"'
        . ' data-points="' . $points_attr . '"'
        . ' data-track="' . $track_attr . '"'
        . ' data-track-color="' . $color_attr . '"'
        . ' data-reach-tracks="' . $rt_attr . '"'
        . ' data-site-config-url="' . $site_config_attr . '"'
        . $gauge_id_attr
        . '></div>';
    return true;
}
