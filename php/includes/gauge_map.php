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
 * Render a Leaflet map block with labelled markers + optional river track.
 *
 * @param array<string,string> $points       Label → "lat,lon" (e.g. 'Gauge' => '44.56,-123.25').
 * @param ?string              $geom         Reach track "lon lat,lon lat,..." or null.
 * @param string               $track_color  CSS colour for the track polyline.
 * @return bool  True if a map was emitted. Caller uses this to decide whether
 *               to enqueue leaflet.js + feature-map.js at end of body.
 */
function gm_render_map(array $points, ?string $geom = null, string $track_color = '#2196F3'): bool {
    if (empty($points) && empty($geom)) {
        return false;
    }

    $track_json = 'null';
    if ($geom) {
        $track = [];
        foreach (explode(',', $geom) as $pair) {
            $parts = preg_split('/\s+/', trim($pair));
            if (count($parts) === 2) {
                $track[] = [(float)$parts[1], (float)$parts[0]];
            }
        }
        if ($track) {
            $track_json = json_encode($track);
        }
    }

    $points_attr = htmlspecialchars((string)json_encode($points), ENT_QUOTES, 'UTF-8');
    $track_attr  = htmlspecialchars($track_json, ENT_QUOTES, 'UTF-8');
    $color_attr  = htmlspecialchars($track_color, ENT_QUOTES, 'UTF-8');

    // leaflet.css sits at <docroot>/static/leaflet.css. __DIR__ here is
    // <docroot>/includes so we hop up one level.
    $css_path = __DIR__ . '/../static/leaflet.css';
    $leaflet_css = @file_get_contents($css_path);
    if ($leaflet_css !== false) {
        echo '<style>' . $leaflet_css . '</style>';
    }
    echo '<div id="feature-map"'
        . ' style="height:350px;margin-top:1rem;border:1px solid #ccc"'
        . ' data-points="' . $points_attr . '"'
        . ' data-track="' . $track_attr . '"'
        . ' data-track-color="' . $color_attr . '"'
        . '></div>';
    return true;
}
