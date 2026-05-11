# `/tpw/` — Trace Color Comparison Tool

A throwaway, self-contained page that renders the same set of reach
traces against three Leaflet base layers (OpenTopoMap, OpenStreetMap,
Esri Satellite) side-by-side, so trace colors / casing / line-weight
tweaks can be A/B'd against every basemap at once.

Built in 2026-05 while tuning the production `static/map.js` palette;
archived here in case the question "how does this color look on all
three basemaps?" comes up again.

## Files

| File | Role |
|---|---|
| `index.html` | Page shell, control bar, three labeled map cells, legend. |
| `style.css` | Dark chrome, 3-column responsive grid, control + legend layout. CSS variables `--col-{low,okay,high,unknown}` drive the legend swatches and are rewritten by JS when the palette changes. |
| `map3.js` | Builds three Leaflet maps from the same `reaches-geom.json` + `reaches-state.json`, mirrors pan/zoom between them, and restyles every reach when the control bar changes. |

## Deploying

The tool is just three static files dropped into `public_html/tpw/`:

```bash
mkdir -p /home/pat/public_html/tpw
cp docs/tpw/{index.html,style.css,map3.js} /home/pat/public_html/tpw/
```

It pulls Leaflet from `/static/leaflet.{js,css}` and the reach data from
`/static/reaches-geom.json` + `/static/reaches-state.json`, which are
produced by the regular `levels build`. No PHP, no server-side
dependencies.

### **Gotcha: `levels build` will delete these files.**

`build.py:_sweep_orphans` walks every file under `public_html/` and
unlinks anything the build didn't produce. Anything dropped into
`/tpw/` survives only until the next `kayak-pipeline.timer` firing
(hourly). Two ways around it:

1. **Use it for a short window.** Deploy, do your evaluation in the
   next hour, then either port the chosen tuning into production
   `static/map.js` or accept that you'll need to re-deploy after the
   sweep.
2. **Make it durable.** Either (a) move `tpw/` into the repo and
   extend `_deploy_source_files` in `build.py:1812` to copy it
   alongside `static/` and `php/`, or (b) add a preserve-list to
   `_sweep_orphans` in `build.py:2054`. Neither is wired up — both are
   a ~6-line change if you want them.

## Controls bar

The form at the top of the page drives every visual knob; settings
round-trip through the URL hash so a tuned variant is shareable.

| Param | Values | Hash key |
|---|---|---|
| Palette | `current`, `v2`, `v3`, `bold` | `p=` |
| Line weight | 3–7 | `w=` |
| Casing | `none`, `light`, `strong`, `double` | `c=` |
| Dashed status | `none`, `low`, `okay`, `high`, `unknown` | `d=` |
| Hover sync | on/off | `h=1` |
| Zoom-aware weight | on/off | `z=1` |
| Hide casing on OSM | on/off | `o=1` |

The hash only includes non-default keys, so `/tpw/` with no hash is the
"current production" look and `/tpw/#p=v2&w=3&c=strong&h=1&z=1` is what
got ported into `static/map.js` on 2026-05-11.

### Palettes (`PALETTES` in `map3.js`)

```js
current: {low:'#ff9800', okay:'#00c853', high:'#e53935', unknown:'#2196f3'}
v2:      {low:'#ff6d00', okay:'#76ff03', high:'#ff1744', unknown:'#00b0ff'}
v3:      {low:'#ff6d00', okay:'#aeea00', high:'#d50000', unknown:'#00e5ff'}
bold:    {low:'#ff6d00', okay:'#76ff03', high:'#ff1744', unknown:'#d500f9'}
```

To add a new palette: add an entry to `PALETTES`, then an
`<option>` to the `palette` `<select>` in `index.html`. No other
plumbing needed.

### Casing modes (`CASING_MODES` in `map3.js`)

```js
none:   null                                              // no halo
light:  {color:'#000', extra:2, opacity:0.50}             // production-pre-2026-05
strong: {color:'#000', extra:2, opacity:0.75}             // shipped 2026-05
double: {color:'#000', extra:4, opacity:0.85}             // + white inner halo (text-stroke)
```

`extra` is the per-side casing pixel growth above the line weight;
`double` also stacks a white inner casing (`INNER_CASING`) between the
outer halo and the colored line.

### Behavior knobs

- **Hover sync** — hovering a reach on any map fattens it on all three
  (line weight `+3`, casing tracks). Uses the wide invisible "hit"
  polyline so weight-3 lines are easy to land on. Looks up sibling
  layers via `reachIndex`, keyed by `properties.id`.
- **Zoom-aware weight** — `weightForZoom` bumps the base weight by +1
  at zoom ≥ 9 and +2 at zoom ≥ 11. Re-applies on every `zoomend`.
- **Hide casing on OSM** — OSM's near-white background already
  contrasts cleanly with saturated colors; the dark halo just adds
  noise. Toggle leaves Topo and Satellite untouched.

## Rebuilding from scratch (if you've lost everything)

The three files are self-contained. The minimum to recreate the tool:

1. Three labeled `<div class="map">` elements in `index.html` with
   `data-base="topo|street|sat"`.
2. A form with the controls listed above, bound to `settings` via
   `input`/`change` events.
3. `map3.js` loop:
   - `fetch('/static/reaches-geom.json')` + `reaches-state.json`.
   - For each base layer, `L.map(div)` + `L.tileLayer(...)`.
   - For each reach: a colored `L.polyline` + a dark casing polyline
     + an invisible wide "hit" polyline.
   - Three-pass `addLayer` order to a shared `L.layerGroup`: casings
     first, then lines, then hits (z-order matters — interleaving lets
     a later reach's casing draw on top of an earlier reach's line,
     which turns state-wide views dark).
   - Mirror `move`/`zoom` between the three `L.map` instances with a
     `syncing` flag.

The `applyStyles()` function in `map3.js` is the part worth reading
first — it's a clear walk through how lines, casings, dashes, and the
per-basemap casing override fit together.

## Why this is throwaway

`/tpw/` was a tuning tool, not a feature. Once a palette/casing combo
won, the chosen values landed in `static/map.js` (see commit log around
2026-05) and `/tpw/` was deleted. Keep this archive only as long as
the question is likely to come up again.
