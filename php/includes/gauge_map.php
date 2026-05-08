<?php
declare(strict_types=1);
/**
 * Shared Leaflet map emitter for gauge/reach detail pages.
 *
 * Emits an inline <style> block (from static/leaflet.css) and a div that
 * static/feature-map.js picks up after page load. The caller decides which
 * labelled points to show and whether to attach a river track polyline.
 */

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
 * @param array<int,array{id:int,name:string,geom:string}> $reach_tracks
 *               Optional list of clickable reach tracks (LineString geoms
 *               only — Point reaches should be skipped by the caller).
 * @return bool  True if a map was emitted. Caller uses this to decide whether
 *               to enqueue leaflet.js + feature-map.js at end of body.
 */
function gm_render_map(
    array $points,
    ?string $geom = null,
    string $track_color = '#2196F3',
    array $reach_tracks = []
): bool {
    if (empty($points) && empty($geom) && empty($reach_tracks)) {
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
    if ($geom) {
        $track = $parse_geom($geom);
        if ($track) {
            $track_json = (string)json_encode($track);
        }
    }

    $rt_payload = [];
    foreach ($reach_tracks as $rt) {
        $coords = $parse_geom((string)($rt['geom'] ?? ''));
        if (count($coords) >= 2) {
            $rt_payload[] = [
                'id' => (int)$rt['id'],
                'name' => (string)$rt['name'],
                'location' => (string)($rt['location'] ?? ''),
                'classes' => (string)($rt['classes'] ?? ''),
                'status' => (string)($rt['status'] ?? 'unknown'),
                'points' => $coords,
            ];
        }
    }
    $rt_json = $rt_payload ? (string)json_encode($rt_payload) : '[]';

    $points_attr = htmlspecialchars((string)json_encode($points), ENT_QUOTES, 'UTF-8');
    $track_attr  = htmlspecialchars($track_json, ENT_QUOTES, 'UTF-8');
    $color_attr  = htmlspecialchars($track_color, ENT_QUOTES, 'UTF-8');
    $rt_attr     = htmlspecialchars($rt_json, ENT_QUOTES, 'UTF-8');

    // leaflet.css sits at <docroot>/static/leaflet.css. __DIR__ here is
    // <docroot>/includes so we hop up one level.
    $css_path = __DIR__ . '/../static/leaflet.css';
    $leaflet_css = @file_get_contents($css_path);
    if ($leaflet_css !== false) {
        echo '<style>' . $leaflet_css . '</style>';
    }
    // Popup styles for clickable reach tracks (gauge page). Mirrors the
    // map.html popup so the look is consistent across pages.
    echo '<style>'
        . '.leaflet-popup-content:has(.reach-popup){margin:0}'
        . '.reach-popup{display:block;color:var(--c-text);text-decoration:none;padding:10px 14px;border-radius:8px;cursor:pointer}'
        . '.reach-popup:hover{background:var(--c-hover)}'
        . '.reach-popup:focus-visible{outline:2px solid var(--c-link);outline-offset:-2px;background:var(--c-hover)}'
        . '.reach-popup .rp-name{font-weight:700;font-size:.95rem;line-height:1.3}'
        . '.reach-popup .rp-loc{font-size:.85rem;color:var(--c-text-muted);margin-top:2px}'
        . '.reach-popup .rp-cls{font-size:.85rem;color:var(--c-text-muted);margin-top:2px}'
        . '</style>';
    echo '<div id="feature-map"'
        . ' style="height:350px;margin-top:1rem;border:1px solid #ccc"'
        . ' data-points="' . $points_attr . '"'
        . ' data-track="' . $track_attr . '"'
        . ' data-track-color="' . $color_attr . '"'
        . ' data-reach-tracks="' . $rt_attr . '"'
        . '></div>';
    return true;
}
