// Leaflet initialiser for gauge + reach detail pages.
//
// Reads an element with id="feature-map" (or legacy id="reach-map") carrying:
//   data-points       JSON object {Label: "lat,lon", ...} — labelled markers.
//   data-track        JSON array [[lat,lon], ...] — river polyline, or null.
//   data-track-color  CSS colour for the polyline (default #2196F3).
//   data-reach-tracks JSON array [{id, name, points: [[lat,lon],...]}, ...]
//                     — clickable per-reach polylines, each opens a popup
//                     linking to /description.php?id=<id>. Used on the
//                     gauge page; omitted on reach pages.
//   data-osmb-obstructions-url / data-osmb-dams-url / data-osmb-access-url
//                     — Oregon SMB overlay GeoJSON URLs. Empty when the
//                     nightly fetcher hasn't landed the file yet; absent
//                     URLs mean "don't register that overlay". The layers
//                     default OFF and lazy-fetch on first toggle, so an
//                     untoggled page incurs zero overlay bandwidth.
//
// OSMB rendering logic (shapes, popups, link URLs) is intentionally
// duplicated from static/map.js — both consumers are small enough that
// a one-file refactor isn't worth the IIFE-global plumbing yet. If a
// third consumer appears, extract to static/osmb-layers.js.
(function () {
  'use strict';
  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : s;
    return d.innerHTML;
  }
  function fmtAge(ms) {
    if (ms < 0) return '';
    if (ms < 60000) return 'just now';
    if (ms < 3600000) return Math.round(ms / 60000) + ' min ago';
    if (ms < 86400000) return Math.round(ms / 3600000) + ' hr ago';
    return Math.round(ms / 86400000) + ' days ago';
  }
  const el =
    document.getElementById('feature-map') ||
    document.getElementById('reach-map');
  if (!el) return;
  const pts = JSON.parse(el.dataset.points);
  const track = el.dataset.track ? JSON.parse(el.dataset.track) : null;
  const trackColor = el.dataset.trackColor || '#2196F3';
  const reachTracks = el.dataset.reachTracks
    ? JSON.parse(el.dataset.reachTracks)
    : [];
  const map = L.map(el);
  const street = L.tileLayer(
    'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    {
      attribution: 'OpenStreetMap',
      maxZoom: 19,
    },
  );
  const topo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    attribution: 'OpenTopoMap',
    maxZoom: 17,
  });
  const satellite = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {
      attribution: 'Esri',
      maxZoom: 19,
    },
  );
  topo.addTo(map);

  // OSMB overlays — see header comment. Layers start empty and get
  // populated on first overlayadd; default OFF (no addTo before the
  // control is built). bounds[] below is intentionally NOT updated with
  // OSMB markers — toggling on must not pan/zoom the map.
  const OSMB_DAM_URL =
    'https://www.oregon.gov/osmb/boating-facilities/Pages/Maps-and-Apps.aspx';
  const OSMB_OBSTRUCTION_URL =
    'https://geo.maps.arcgis.com/apps/dashboards/59f4dfde321f447b9245a1451c83e054';
  const OSMB_ACCESS_URL =
    'https://experience.arcgis.com/experience/72308dd6b893451690a14437cde89be8';
  const OSMB_CANVAS_RENDERER = L.canvas();
  const OSMB_HIT_RADIUS = 14;
  const OSMB_LAYER_DEFS = [
    {
      key: 'obstructions',
      label: 'Obstructions',
      color: '#ff00ff',
      attr: 'osmbObstructionsUrl',
      shape: 'triangle',
      size: 16,
      zIndex: 200,
      popup: obstructionPopup,
    },
    {
      key: 'dams',
      label: 'Dams / weirs',
      color: '#6a1b9a',
      attr: 'osmbDamsUrl',
      shape: 'diamond',
      size: 14,
      zIndex: 100,
      popup: damPopup,
    },
    {
      key: 'access',
      label: 'Access sites',
      color: '#1b5e20',
      attr: 'osmbAccessUrl',
      shape: 'circle',
      size: 5,
      zIndex: 0,
      popup: accessPopup,
    },
  ];
  const osmbLayers = {};
  const osmbUrls = {};
  const osmbLoaded = {};
  const overlays = {};
  OSMB_LAYER_DEFS.forEach(function (d) {
    const url = el.dataset[d.attr] || '';
    if (!url) return;
    osmbUrls[d.key] = url;
    osmbLayers[d.key] = L.layerGroup();
    const swatch =
      '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' +
      d.color +
      ';border:1px solid rgba(0,0,0,.15);margin-right:6px;vertical-align:middle"></span>';
    overlays[swatch + d.label] = osmbLayers[d.key];
  });

  L.control
    .layers({ Topo: topo, Street: street, Satellite: satellite }, overlays, {
      collapsed: true,
    })
    .addTo(map);
  L.control.scale({ imperial: true, metric: false }).addTo(map);

  // Lazy-fetch on first toggle. Markers stream in after the layer is
  // already on the map; the brief "empty layer for a second" gap is
  // fine on a typical connection. A failed fetch resets osmbLoaded so
  // a re-toggle retries instead of permanently 404'ing.
  map.on('overlayadd', function (e) {
    for (const key in osmbLayers) {
      if (e.layer === osmbLayers[key] && !osmbLoaded[key]) {
        osmbLoaded[key] = true;
        const def = OSMB_LAYER_DEFS.filter(function (d) {
          return d.key === key;
        })[0];
        fetch(osmbUrls[key])
          .then(function (r) {
            if (!r.ok) throw new Error('osmb ' + key + ' ' + r.status);
            return r.json();
          })
          .then(function (data) {
            populateOsmbLayer(osmbLayers[key], data, def);
          })
          .catch(function (err) {
            console.warn('osmb ' + key + ' fetch failed:', err);
            osmbLoaded[key] = false;
          });
        break;
      }
    }
  });

  function populateOsmbLayer(lg, data, def) {
    const features = data?.features || [];
    for (let i = 0; i < features.length; i++) {
      const f = features[i];
      const coords = f.geometry?.coordinates;
      if (!coords || coords.length < 2) continue;
      const ll = L.latLng(coords[1], coords[0]);
      const props = f.properties || {};
      if (def.shape === 'circle') {
        L.circleMarker(ll, {
          renderer: OSMB_CANVAS_RENDERER,
          radius: def.size,
          fillColor: def.color,
          color: '#222',
          weight: 1,
          fillOpacity: 0.85,
          interactive: false,
        }).addTo(lg);
        const hit = L.circleMarker(ll, {
          renderer: OSMB_CANVAS_RENDERER,
          radius: OSMB_HIT_RADIUS,
          opacity: 0,
          fillOpacity: 0,
          interactive: true,
        }).addTo(lg);
        hit.bindPopup(def.popup.bind(null, props));
      } else {
        const marker = L.marker(ll, {
          icon: makeShapeIcon(def.shape, def.size, def.color),
          zIndexOffset: def.zIndex || 0,
          keyboard: false,
        }).addTo(lg);
        marker.bindPopup(def.popup.bind(null, props));
      }
    }
  }

  function makeShapeIcon(shape, size, color) {
    const box = 28;
    const c = box / 2;
    const half = size / 2;
    let pts = '';
    if (shape === 'triangle') {
      const halfW = half * 0.866;
      pts =
        c +
        ',' +
        (c - half) +
        ' ' +
        (c + halfW) +
        ',' +
        (c + half * 0.5) +
        ' ' +
        (c - halfW) +
        ',' +
        (c + half * 0.5);
    } else if (shape === 'diamond') {
      pts =
        c +
        ',' +
        (c - half) +
        ' ' +
        (c + half) +
        ',' +
        c +
        ' ' +
        c +
        ',' +
        (c + half) +
        ' ' +
        (c - half) +
        ',' +
        c;
    }
    const svg =
      '<svg width="' +
      box +
      '" height="' +
      box +
      '" viewBox="0 0 ' +
      box +
      ' ' +
      box +
      '" xmlns="http://www.w3.org/2000/svg">' +
      '<polygon points="' +
      pts +
      '" fill="' +
      color +
      '" stroke="#222" stroke-width="1" stroke-linejoin="round"/>' +
      '</svg>';
    return L.divIcon({
      className: 'osmb-icon osmb-icon--' + shape,
      html: svg,
      iconSize: [box, box],
      iconAnchor: [c, c],
      popupAnchor: [0, -half],
    });
  }

  function obstructionPopup(p) {
    const title = esc(p.obslocation || p.waterbody || 'Obstruction');
    const sub = [p.waterbody, p.waterbodysec]
      .filter(Boolean)
      .map(esc)
      .join(' · ');
    const desc = esc(p.obsdescript || '');
    const ageMs = p.recordtime ? Date.now() - Number(p.recordtime) : -1;
    const age =
      ageMs >= 0
        ? '<span class="rp-time">' + esc(fmtAge(ageMs)) + '</span>'
        : '';
    let html =
      '<a class="reach-popup" href="' +
      OSMB_OBSTRUCTION_URL +
      '" target="_blank" rel="noopener">' +
      '<div class="rp-name">' +
      title +
      '</div>';
    if (sub) html += '<div class="rp-sub">' + sub + '</div>';
    if (desc) html += '<div class="rp-reading">' + desc + '</div>';
    if (age) html += '<div class="rp-footer">' + age + '</div>';
    html += '</a>';
    return html;
  }

  function damPopup(p) {
    const title = esc(p.damname || 'Dam');
    const sub = esc(p.waterbody || '');
    const sizeBits = [];
    if (p.damheight) sizeBits.push(p.damheight + ' ft tall');
    if (p.damwidth) sizeBits.push(p.damwidth + ' ft wide');
    const portage = esc(p.portagedesc || p.navigate || '');
    let html =
      '<a class="reach-popup" href="' +
      OSMB_DAM_URL +
      '" target="_blank" rel="noopener">' +
      '<div class="rp-name">' +
      title +
      '</div>';
    if (sub) html += '<div class="rp-sub">' + sub + '</div>';
    if (sizeBits.length)
      html += '<div class="rp-reading">' + esc(sizeBits.join(' · ')) + '</div>';
    if (portage) html += '<div class="rp-sub">' + portage + '</div>';
    html += '</a>';
    return html;
  }

  function accessPopup(p) {
    const title = esc(p.name || 'Access site');
    const sub = esc(p.waterway_name || '');
    const facility = esc(
      [p.facility_type, p.launch_type].filter(Boolean).join(' · '),
    );
    let html =
      '<a class="reach-popup" href="' +
      OSMB_ACCESS_URL +
      '" target="_blank" rel="noopener">' +
      '<div class="rp-name">' +
      title +
      '</div>';
    if (sub) html += '<div class="rp-sub">' + sub + '</div>';
    if (facility) html += '<div class="rp-reading">' + facility + '</div>';
    html += '</a>';
    return html;
  }
  const bounds = [];
  const colors = {
    'Put-in': '#1a7a1a',
    Gauge: '#1b5591',
    'Take-out': '#b30000',
  };
  const gaugeId = parseInt(el.dataset.gaugeId || '0', 10);
  const gaugeH = el.dataset.gaugeH || '';
  for (const k in pts) {
    const c = pts[k].split(',');
    const ll = [parseFloat(c[0]), parseFloat(c[1])];
    const color = colors[k] || '#1b5591';
    const dot = L.circleMarker(ll, {
      radius: 6,
      fillColor: color,
      color: '#222',
      weight: 1,
      fillOpacity: 0.95,
    }).addTo(map);
    dot.bindTooltip(k, {
      permanent: true,
      direction: 'right',
      offset: [6, 0],
      className: 'map-label',
      interactive: true,
    });
    bounds.push(ll);
    (function (lat, lon, label) {
      const onClick = function () {
        if (label === 'Gauge' && gaugeId) {
          window.location.href = '/gauge.php?h=' + gaugeH;
        } else {
          window.open(
            'https://www.google.com/maps?q=' + lat + ',' + lon,
            '_blank',
          );
        }
      };
      dot.on('click', onClick);
      const tt = dot.getTooltip();
      if (tt) tt.on('click', onClick);
    })(ll[0], ll[1], k);
  }
  if (track) {
    L.polyline(track, { color: trackColor, weight: 6, opacity: 0.6 }).addTo(
      map,
    );
    track.forEach(function (p) {
      bounds.push(p);
    });
    // Connect put-in/take-out markers to trace endpoints with dashed lines
    const dash = { color: '#666', weight: 2, opacity: 0.6, dashArray: '6,6' };
    if (pts['Put-in']) {
      const pi = pts['Put-in'].split(',');
      const piLL = [parseFloat(pi[0]), parseFloat(pi[1])];
      L.polyline([piLL, track[0]], dash).addTo(map);
    }
    if (pts['Take-out']) {
      const to = pts['Take-out'].split(',');
      const toLL = [parseFloat(to[0]), parseFloat(to[1])];
      L.polyline([track[track.length - 1], toLL], dash).addTo(map);
    }
  }
  // Per-reach clickable polylines for the gauge page. Coloured by status
  // (low/okay/high), with a dark casing for contrast and a fat invisible
  // hit polyline so finger taps register reliably on touch devices.
  // Mirrors the styling and hover behaviour of the main /map.html.
  if (reachTracks.length) {
    const COLORS = {
      low: '#ff6d00',
      okay: '#76ff03',
      high: '#ff1744',
      unknown: '#00b0ff',
    };
    const REST_LINE = { weight: 4, opacity: 1.0 };
    const HOVER_LINE = { weight: 7, opacity: 1.0 };
    const REST_CASING = {
      color: '#1a1a1a',
      weight: 5,
      opacity: 0.5,
      lineJoin: 'round',
      lineCap: 'round',
      interactive: false,
    };
    const HOVER_CASING = { weight: 9 };
    // Casings render below the colored line so bringToFront on hover doesn't
    // disrupt mouseout (same pane trick used on /map.html).
    map.createPane('reach-casings');
    map.getPane('reach-casings').style.zIndex = '400';
    map.getPane('reach-casings').style.pointerEvents = 'none';
    map.createPane('reaches');
    map.getPane('reaches').style.zIndex = '410';
    map.createPane('reach-hits');
    map.getPane('reach-hits').style.zIndex = '420';

    reachTracks.forEach(function (rt) {
      const color = COLORS[rt.status] || COLORS.unknown;
      const visible = L.polyline(rt.points, {
        color: color,
        weight: REST_LINE.weight,
        opacity: REST_LINE.opacity,
        lineJoin: 'round',
        lineCap: 'round',
        pane: 'reaches',
      }).addTo(map);
      const casing = L.polyline(
        rt.points,
        Object.assign({}, REST_CASING, { pane: 'reach-casings' }),
      ).addTo(map);
      const hit = L.polyline(rt.points, {
        weight: 18,
        opacity: 0,
        interactive: true,
        pane: 'reach-hits',
        lineCap: 'round',
        lineJoin: 'round',
      }).addTo(map);

      const html =
        '<a class="reach-popup" href="/description.php?h=' +
        rt.h +
        '">' +
        '<div class="rp-name">' +
        esc(rt.name) +
        '</div>' +
        (rt.location
          ? '<div class="rp-loc">' + esc(rt.location) + '</div>'
          : '') +
        (rt.classes
          ? '<div class="rp-cls">Class ' + esc(rt.classes) + '</div>'
          : '') +
        '</a>';
      hit.bindPopup(html);

      hit.on('mouseover', function () {
        visible.setStyle(HOVER_LINE);
        casing.setStyle(HOVER_CASING);
        visible.bringToFront();
      });
      hit.on('mouseout', function () {
        visible.setStyle(REST_LINE);
        casing.setStyle({ weight: REST_CASING.weight });
      });
      rt.points.forEach(function (p) {
        bounds.push(p);
      });
    });
  }

  if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [40, 40] });
  } else if (bounds.length === 1) {
    map.setView(bounds[0], 13);
  }

  // Expose the map instance on the element so siblings (e.g.
  // static/gradient-profile.js) can drop a hover marker. Convention
  // only — no other code in the project should poke at this.
  el._kayakMap = map;

  // Right-click anywhere on the map pops up the cursor lat/lon with
  // a Copy button. Left-click already routes through marker handlers
  // (which open Google Maps), so contextmenu is the natural channel
  // for grabbing an arbitrary on-channel waypoint during reach-coord
  // refinement.
  //
  // Listen on the container DOM via L.DomEvent.on (a thin wrapper over
  // addEventListener), not map.on('contextmenu'): Leaflet only fires
  // its synthetic contextmenu — and only then runs preventDefault — on
  // hit-tested targets that have a listener, so right-clicks landing
  // on tile <img>s, controls, or unlistening marker icons bypass the
  // Leaflet path entirely and Chrome's native menu still shows. A
  // native listener on the container fires for every contextmenu that
  // bubbles up, and preventDefault always lands on the original event.
  //
  // CSP-safe: createElement + textContent + addEventListener throughout
  // (no innerHTML for the popup content, no inline on* attrs).
  L.DomEvent.on(map.getContainer(), 'contextmenu', function (ev) {
    L.DomEvent.preventDefault(ev);
    L.DomEvent.stopPropagation(ev);
    const latlng = map.containerPointToLatLng(
      map.mouseEventToContainerPoint(ev),
    );
    const lat = latlng.lat.toFixed(6);
    const lng = latlng.lng.toFixed(6);
    const coords = lat + ', ' + lng;
    const wrap = document.createElement('div');
    const code = document.createElement('code');
    code.textContent = coords;
    code.style.fontSize = '13px';
    code.style.userSelect = 'all';
    code.style.marginRight = '8px';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Copy';
    btn.style.cursor = 'pointer';
    btn.addEventListener('click', function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(coords).then(
          function () {
            btn.textContent = 'Copied';
          },
          function () {
            btn.textContent = 'Copy failed';
          },
        );
      } else {
        btn.textContent = 'Clipboard unavailable';
      }
    });
    wrap.appendChild(code);
    wrap.appendChild(btn);
    L.popup({ className: 'latlon-popup' })
      .setLatLng(latlng)
      .setContent(wrap)
      .openOn(map);
  });
})();
