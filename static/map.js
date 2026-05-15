/* Interactive river map.
 *
 * Fetches two files in parallel:
 *   - reaches-geom.json (static geometry + metadata, long-cached)
 *   - reaches-state.json (current status per reach id, short-cached)
 * Merges them in-browser and renders with Leaflet plus a filter panel
 * that toggles visibility by current status and whitewater class tier.
 * Filter state is persisted in the URL hash so filtered views are shareable.
 */
(function(){
'use strict';
// Tuned 2026-05 against topo + satellite + street basemaps:
//   low   #ff6d00 — Material orange A700, deeper than #ff9800 so it
//                   separates from topo's tan terrain that washed out
//                   lighter oranges.
//   okay  #76ff03 — Material light-green A400 (chartreuse); sits
//                   outside the forest-green band where the prior
//                   #00c853 blended on topo and satellite.
//   high  #ff1744 — Material red A400, hotter than #e53935 to pop on
//                   satellite without losing the "danger" reading.
//   unkn  #00b0ff — Material light-blue A400, cyan-shifted to stay
//                   distinct from satellite's blue-grey water tones.
const COLORS={low:'#ff6d00',okay:'#76ff03',high:'#ff1744',unknown:'#00b0ff'};
const STATUSES=['low','okay','high','unknown'];
const CLASS_TIERS=['I','II','III','IV','V','?'];
const DEFAULT_VIEW=[44.0,-120.5];
const DEFAULT_ZOOM=7;

// Item 1 of docs/PLAN_map_and_ui_tweaks.md: hover-opens-popup is desktop-
// only. Touch-only devices keep tap-to-open (Leaflet's built-in click
// behavior). (hover: hover) matches devices whose primary input can
// hover (mice, trackpads); (pointer: fine) gates out touchscreens-with-
// stylus that emit fake hovers. Evaluated once at module load.
const DESKTOP_HOVER=window.matchMedia('(hover: hover) and (pointer: fine)').matches;
// Grace window between cursor leaving both surfaces (trace + popup
// interior) and the popup actually closing. Sized for a normal slow
// traversal from trace edge into popup body. Decision §5 of the plan;
// tuning band 100–200 ms if it feels wrong in the browser.
const POPUP_CLOSE_GRACE_MS=150;

// Item 2 of docs/PLAN_map_and_ui_tweaks.md — gauge markers.
//   ZOOM_THRESHOLD: state-wide views (z<9) get tiny dots; zoom-in
//     (z>=9) gets the larger marker so the user can read the cluster.
//   RADIUS_LOW/HIGH: visible marker radius in pixels at each tier.
//   HIT_RADIUS: transparent overlay sized for a 44 px-ish mobile tap
//     target regardless of zoom — mirrors HIT_POINT for point reaches.
const GAUGE_ZOOM_THRESHOLD=9;
const GAUGE_RADIUS_LOW=3;
const GAUGE_RADIUS_HIGH=7;
const GAUGE_HIT_RADIUS=14;
// Tagged-stale gauge markers (>1 d <=7 d old) render at reduced
// opacity to telegraph "data may be old without being expired".
// 0.55 mirrors `.rp-stale` opacity in style.css for the reach popup.
const GAUGE_STALE_OPACITY=0.55;

function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}

// reaches-state.json was once {id: "status"}; it's now {id: {s, t, v, u, d, ts}}.
// Tolerate both during the cache-overlap window where an old map.js may meet
// new state JSON or vice versa.
function readEntry(state,id){
  const v=state[id];
  if(typeof v==='string')return {s:v};
  return v||{s:'unknown'};
}

function fmtValue(v,t,u){
  if(t==='flow')return Math.round(v).toLocaleString()+' '+u;
  return Number(v).toFixed(1)+' '+u;
}

// Match description.php's "stable" threshold (|d| < 0.5) regardless of data
// type. Below that, render no trend at all rather than a misleading arrow.
function fmtDelta(d,t,u){
  if(d==null||Math.abs(d)<0.5)return '';
  const arrow=d>0?'↑':'↓';
  const mag=t==='flow'
    ? Math.abs(Math.round(d)).toLocaleString()
    : Math.abs(d).toFixed(1);
  return arrow+' '+mag+' '+u+'/hr';
}

function fmtAge(ms){
  if(ms<0)return '';
  if(ms<60000)return 'just now';
  if(ms<3600000)return Math.round(ms/60000)+' min ago';
  if(ms<86400000)return Math.round(ms/3600000)+' hr ago';
  return Math.round(ms/86400000)+' days ago';
}

const STALE_MS=24*3600*1000;

function parseHash(){
  // gauges defaults to true (Decision §2 — gauges visible by default).
  // Only ?gauges=off persists in the hash; toggling back on clears it
  // so the common-case URL stays short.
  const out={s:null,c:null,gauges:true};
  const h=(location.hash||'').replace(/^#/,'');
  if(!h)return out;
  h.split('&').forEach(function(kv){
    const eq=kv.indexOf('=');
    if(eq<0)return;
    const k=kv.slice(0,eq), v=kv.slice(eq+1);
    if(k==='s'||k==='c'){
      out[k]=v===''?[]:decodeURIComponent(v).split(',').filter(Boolean);
    }else if(k==='gauges' && v==='off'){
      out.gauges=false;
    }
  });
  return out;
}

function writeHash(sSet,cSet,showGauges){
  const parts=[];
  if(sSet.size!==STATUSES.length)parts.push('s='+Array.from(sSet).join(','));
  if(cSet.size!==CLASS_TIERS.length)parts.push('c='+Array.from(cSet).join(','));
  if(showGauges===false)parts.push('gauges=off');
  const hash=parts.length?('#'+parts.join('&')):'';
  if(hash!==location.hash){
    history.replaceState(null,'',location.pathname+location.search+hash);
  }
}

const mapEl=document.getElementById('map');
const geomUrl=mapEl.dataset.geomUrl;
const stateUrl=mapEl.dataset.stateUrl;
// Gauge layer URLs (Item 2 of map_and_ui_tweaks). Empty string when the
// builder didn't wire a gauge layer (older snapshots, tests). When
// either is missing, renderMap skips the gauge layer wholesale.
const gaugesGeomUrl=mapEl.dataset.gaugesGeomUrl||'';
const gaugesStateUrl=mapEl.dataset.gaugesStateUrl||'';

const map=L.map('map');
const topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{maxZoom:17,attribution:'OpenTopoMap'});
const street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'OpenStreetMap'});
const sat=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,attribution:'Esri'});
street.addTo(map);
L.control.layers({Topo:topo,Street:street,Satellite:sat}).addTo(map);

// All overlays render into the default overlayPane in a single SVG.
// iPhone Safari silently dropped overlays when they were split across
// custom panes (iPad sends a desktop UA and tolerated it).

function fail(msg){
  map.setView(DEFAULT_VIEW,DEFAULT_ZOOM);
  const ctl=L.control({position:'topright'});
  ctl.onAdd=function(){const d=L.DomUtil.create('div','map-filter');d.innerHTML='<div class="mf-err">'+esc(msg)+'</div>';return d;};
  ctl.addTo(map);
}

// Reach JSON must succeed; gauge JSON is best-effort — a 404 / parse
// error logs a warning and the map still renders without the layer
// (Item 2c). Resolving to null lets renderMap treat absence uniformly.
function fetchOptional(url, label){
  if(!url)return Promise.resolve(null);
  return fetch(url)
    .then(function(r){
      if(!r.ok){console.warn('map: '+label+' fetch '+r.status);return null;}
      return r.json();
    })
    .catch(function(e){console.warn('map: '+label+' fetch failed:',e);return null;});
}

Promise.all([
  fetch(geomUrl).then(function(r){if(!r.ok)throw new Error('geom '+r.status);return r.json();}),
  fetch(stateUrl).then(function(r){if(!r.ok)throw new Error('state '+r.status);return r.json();}),
  fetchOptional(gaugesGeomUrl,'gauges-geom'),
  fetchOptional(gaugesStateUrl,'gauges-state'),
]).then(function(res){
  renderMap(res[0],res[1],res[2],res[3]);
}).catch(function(e){
  console.error('map data load failed:',e);
  fail('Map data failed to load.');
});

function renderMap(geom,state,gaugesGeom,gaugesState){
  const initial=parseHash();
  const sSet=new Set(initial.s===null?STATUSES:initial.s);
  const cSet=new Set(initial.c===null?CLASS_TIERS:initial.c);
  // Gauge layer is available only when both files loaded cleanly.
  // hasGaugeLayer gates the filter checkbox AND the fitBounds union.
  const hasGaugeLayer = !!(gaugesGeom && gaugesState);
  let showGauges = hasGaugeLayer && initial.gauges;

  // Dark halo casing 2px wider than the colored line at 0.75 opacity.
  // Denser than the prior 0.5 because the line itself dropped from 4 to
  // 3 px — a thinner colored line over a darker halo keeps every status
  // readable across topo's tan terrain and satellite's mixed forest +
  // soil cover. recomputeWeights() below scales both line and casing as
  // the user zooms in, mutating the shared objects so subsequent
  // setStyle calls (mouseover/mouseout) pick up the current zoom.
  const BASE_WEIGHT=3;
  const REST_LINE={weight:BASE_WEIGHT,opacity:1.0};
  const HOVER_LINE={weight:BASE_WEIGHT+3,opacity:1.0};
  const REST_CASING={color:'#000',weight:BASE_WEIGHT+2,opacity:0.75,lineJoin:'round',lineCap:'round',interactive:false};
  const HOVER_CASING={weight:BASE_WEIGHT+5};
  const HIT_LINE={weight:18,opacity:0,interactive:true,lineCap:'round',lineJoin:'round'};
  const HIT_POINT={radius:14,opacity:0,fillOpacity:0,interactive:true};

  function recomputeWeights(){
    let z=map.getZoom();
    if(z==null)z=DEFAULT_ZOOM;
    let w=BASE_WEIGHT;
    if(z>=11)w=BASE_WEIGHT+2;
    else if(z>=9)w=BASE_WEIGHT+1;
    REST_LINE.weight=w;
    HOVER_LINE.weight=w+3;
    REST_CASING.weight=w+2;
    HOVER_CASING.weight=w+5;
  }
  recomputeWeights();

  const layersById={};
  L.geoJSON(geom,{
    style:function(f){
      const s=readEntry(state,f.properties.id).s||'unknown';
      return {color:COLORS[s]||COLORS.unknown,weight:REST_LINE.weight,opacity:REST_LINE.opacity,lineJoin:'round',lineCap:'round'};
    },
    pointToLayer:function(f,ll){
      const s=readEntry(state,f.properties.id).s||'unknown';
      return L.circleMarker(ll,{radius:6,fillColor:COLORS[s]||COLORS.unknown,color:'#333',weight:1,fillOpacity:0.8});
    },
    onEachFeature:function(f,layer){
      const p=f.properties;
      const entry=readEntry(state,p.id);
      const s=entry.s||'unknown';
      const tiers=p.tiers||['?'];
      const classDisplay=tiers.join(' · ');
      // Popup HTML is built lazily via a Leaflet popup-content function
      // so the "X hr ago" text reflects the moment the user opens it,
      // not the page-load time. Closes over p+entry; entry is the
      // snapshot captured at fetch time, which is what we want — the
      // value doesn't update without a refresh either.
      function buildPopup(){
        const dotColor=COLORS[s]||COLORS.unknown;
        let html=
          '<a class="reach-popup" href="/description.php?id='+parseInt(p.id,10)+'">'+
            '<div class="rp-name">'+esc(p.name)+'</div>';
        let ageStr='';
        if('v' in entry){
          const val=fmtValue(entry.v,entry.t,entry.u);
          const delta=fmtDelta(entry.d,entry.t,entry.u);
          const ageMs=entry.ts?(Date.now()-Date.parse(entry.ts)):-1;
          ageStr=ageMs>=0?fmtAge(ageMs):'';
          const stale=ageMs>STALE_MS;
          html+='<div class="rp-reading'+(stale?' rp-stale':'')+'">';
          html+=esc(val);
          if(delta)html+=' <span class="rp-trend">'+esc(delta)+'</span>';
          html+='</div>';
        }
        html+='<div class="rp-footer">';
        if(ageStr)html+='<span class="rp-time">'+esc(ageStr)+'</span>';
        html+='<span class="rp-status-text"><span class="rp-dot" style="color:'+dotColor+'">&#9679;</span> '+esc(s)+'</span>';
        if(classDisplay)html+='<span class="rp-tiers">'+esc(classDisplay)+'</span>';
        html+='</div></a>';
        return html;
      }

      layersById[p.id]=layer;
      layer._mfStatus=s;
      layer._mfTiers=tiers;
      // Halo casing rendered beneath the colored line via add-order in
      // refilter() (all casings first, then all lines, then all hits).
      layer._mfCasing=typeof layer.getLatLngs==='function'
        ? L.polyline(layer.getLatLngs(),REST_CASING)
        : null;
      // Fat invisible hit shape added last (renders on top): catches taps
      // anywhere within ~18px of a thin reach line and forwards style
      // updates to the visible colored layer below.
      let hit=null;
      if(typeof layer.getLatLngs==='function'){
        hit=L.polyline(layer.getLatLngs(),HIT_LINE);
      }else if(typeof layer.getLatLng==='function'){
        hit=L.circleMarker(layer.getLatLng(),HIT_POINT);
      }
      layer._mfHit=hit;

      const target=hit||layer;
      target.bindPopup(buildPopup);
      // Two-surface hover tracking for the desktop hover-popup flow.
      // The popup body wraps in <a href="/description.php?id=...">; we
      // need to let the user move from trace into the popup to click,
      // so close only when neither surface is hovered (with a grace
      // window for normal cursor traversal). Touch devices keep
      // Leaflet's built-in click-to-open. Item 1 of
      // docs/PLAN_map_and_ui_tweaks.md.
      let closeTimer=null;
      function scheduleClose(){
        if(closeTimer!==null)clearTimeout(closeTimer);
        closeTimer=setTimeout(function(){
          closeTimer=null;
          if(!layer._mfHovered&&!layer._mfPopupHovered)target.closePopup();
        },POPUP_CLOSE_GRACE_MS);
      }
      function cancelClose(){
        if(closeTimer!==null){clearTimeout(closeTimer);closeTimer=null;}
      }
      target.on('mouseover',function(){
        layer._mfHovered=true;
        layer.setStyle(HOVER_LINE);
        if(layer._mfCasing)layer._mfCasing.setStyle(HOVER_CASING);
        if(DESKTOP_HOVER){cancelClose();target.openPopup();}
      });
      target.on('mouseout',function(){
        layer._mfHovered=false;
        layer.setStyle(REST_LINE);
        if(layer._mfCasing)layer._mfCasing.setStyle({weight:REST_CASING.weight});
        if(DESKTOP_HOVER)scheduleClose();
      });
      // popupopen fires each time the popup is shown; attach hover
      // listeners to the popup DOM node so cancelling/scheduling the
      // close timer keeps the popup alive while the cursor sits on it.
      target.on('popupopen',function(e){
        if(!DESKTOP_HOVER)return;
        const el=e.popup.getElement();
        if(!el)return;
        el.addEventListener('mouseenter',function(){
          layer._mfPopupHovered=true;
          cancelClose();
        });
        el.addEventListener('mouseleave',function(){
          layer._mfPopupHovered=false;
          scheduleClose();
        });
      });
    },
  });

  // Zoom-aware weight: bump the line + casing as the user zooms in so a
  // single reach reads at detail-zoom without making state-wide views
  // feel cluttered. Restyle in-place rather than rebuilding the group;
  // _mfHovered keeps a hovered reach at the bumped (hover) weight even
  // when zoom changes mid-hover.
  map.on('zoomend',function(){
    recomputeWeights();
    for(const id in layersById){
      const lyr=layersById[id];
      if(typeof lyr.setStyle!=='function')continue;
      const hov=lyr._mfHovered;
      lyr.setStyle({weight:hov?HOVER_LINE.weight:REST_LINE.weight});
      if(lyr._mfCasing){
        lyr._mfCasing.setStyle({weight:hov?HOVER_CASING.weight:REST_CASING.weight});
      }
    }
    // Zoom-graded gauge marker radius — visible markers only, hit
    // shapes stay constant so the tap target doesn't shrink at low zoom.
    if(gaugeMarkers.length){
      const r=gaugeRadiusForZoom(map.getZoom());
      for(let i=0;i<gaugeMarkers.length;i++)gaugeMarkers[i].setRadius(r);
    }
  });

  const group=L.layerGroup().addTo(map);

  // Gauge layer (Item 2 of map_and_ui_tweaks). Built only when both
  // gauge JSON files loaded — empty layerGroup otherwise so the rest
  // of the file can treat gaugeLayer as always-present.
  const gaugeLayer=L.layerGroup();
  const gaugeMarkers=[];  // visible circleMarkers, kept for zoom restyle
  if(hasGaugeLayer)buildGaugeLayer(gaugesGeom,gaugesState,gaugeLayer,gaugeMarkers);
  if(hasGaugeLayer && showGauges)gaugeLayer.addTo(map);

  function gaugeRadiusForZoom(z){
    return (z==null||z<GAUGE_ZOOM_THRESHOLD)?GAUGE_RADIUS_LOW:GAUGE_RADIUS_HIGH;
  }

  // Build the visible + hit circleMarker pair for each gauge feature.
  // Mirrors Item 1's hover mechanic so desktop users get the same
  // open-on-hover / close-after-grace behavior on gauge markers as on
  // reach lines. Markers are pushed into ``markerArr`` so the zoomend
  // handler can restyle them when the threshold is crossed.
  function buildGaugeLayer(geom,state,layerGroup,markerArr){
    const features=(geom&&geom.features)||[];
    const initialRadius=gaugeRadiusForZoom(map.getZoom());
    for(let i=0;i<features.length;i++){
      const f=features[i];
      const gid=f.id;
      const coords=f.geometry&&f.geometry.coordinates;
      if(!coords||coords.length!==2)continue;
      const ll=L.latLng(coords[1],coords[0]);
      const entry=state[gid]||{s:'unknown'};
      const status=entry.s||'unknown';
      const stale=!!entry.stale;
      const color=COLORS[status]||COLORS.unknown;
      const baseOpacity=stale?GAUGE_STALE_OPACITY:1.0;
      const baseFillOpacity=stale?GAUGE_STALE_OPACITY:0.85;

      const visible=L.circleMarker(ll,{
        radius:initialRadius,
        fillColor:color,
        color:'#333',
        weight:1,
        opacity:baseOpacity,
        fillOpacity:baseFillOpacity,
        interactive:false,
      });
      markerArr.push(visible);
      layerGroup.addLayer(visible);

      // Transparent 14 px hit shape mirrors HIT_POINT for reaches —
      // gives mobile a reliable tap target even at low zoom where the
      // visible marker shrinks to 3 px.
      const hit=L.circleMarker(ll,{
        radius:GAUGE_HIT_RADIUS,
        opacity:0,
        fillOpacity:0,
        interactive:true,
      });
      layerGroup.addLayer(hit);

      const props=f.properties||{};
      function buildPopup(){
        const ageMs=entry.ts?(Date.now()-Date.parse(entry.ts)):-1;
        const ageStr=ageMs>=0?fmtAge(ageMs):'';
        let html='<a class="reach-popup" href="/gauge.php?id='+parseInt(gid,10)+'">';
        html+='<div class="rp-name">'+esc(props.name||'')+'</div>';
        const subtitleParts=[];
        if(props.river)subtitleParts.push(props.river);
        if(props.location&&props.location!==props.river)subtitleParts.push(props.location);
        if(subtitleParts.length){
          html+='<div class="rp-sub">'+esc(subtitleParts.join(' · '))+'</div>';
        }
        const readings=[];
        if(entry.flow)readings.push(fmtValue(entry.flow.v,'flow',entry.flow.u));
        if(entry.gage)readings.push(fmtValue(entry.gage.v,'gage',entry.gage.u));
        if(entry.temperature)readings.push(fmtValue(entry.temperature.v,'temperature',entry.temperature.u));
        if(readings.length){
          html+='<div class="rp-reading'+(stale?' rp-stale':'')+'">'+esc(readings.join(' · '))+'</div>';
        }
        html+='<div class="rp-footer">';
        if(ageStr)html+='<span class="rp-time">'+esc(ageStr)+'</span>';
        html+='<span class="rp-status-text"><span class="rp-dot" style="color:'+color+'">&#9679;</span> '+esc(status)+'</span>';
        html+='</div></a>';
        return html;
      }
      hit.bindPopup(buildPopup);

      // Two-surface hover mechanic. ``traceHovered`` covers the gauge
      // marker hit shape; ``popupHovered`` covers the popup body — the
      // popup wraps in <a href> so the cursor must traverse from
      // marker → popup to click without the popup closing under it.
      let closeTimer=null;
      let traceHovered=false;
      let popupHovered=false;
      function scheduleClose(){
        if(closeTimer!==null)clearTimeout(closeTimer);
        closeTimer=setTimeout(function(){
          closeTimer=null;
          if(!traceHovered&&!popupHovered)hit.closePopup();
        },POPUP_CLOSE_GRACE_MS);
      }
      function cancelClose(){
        if(closeTimer!==null){clearTimeout(closeTimer);closeTimer=null;}
      }
      hit.on('mouseover',function(){
        traceHovered=true;
        if(DESKTOP_HOVER){cancelClose();hit.openPopup();}
      });
      hit.on('mouseout',function(){
        traceHovered=false;
        if(DESKTOP_HOVER)scheduleClose();
      });
      hit.on('popupopen',function(e){
        if(!DESKTOP_HOVER)return;
        const el=e.popup.getElement();
        if(!el)return;
        el.addEventListener('mouseenter',function(){
          popupHovered=true;
          cancelClose();
        });
        el.addEventListener('mouseleave',function(){
          popupHovered=false;
          scheduleClose();
        });
      });
    }
  }

  function matches(layer){
    if(!sSet.has(layer._mfStatus))return false;
    const tiers=layer._mfTiers;
    for(let i=0;i<tiers.length;i++)if(cSet.has(tiers[i]))return true;
    return false;
  }

  let countEl=null;
  let firstPaint=true;
  function refilter(){
    group.clearLayers();
    const visible=[];
    for(const id in layersById){
      if(matches(layersById[id]))visible.push(layersById[id]);
    }
    // Three passes so all casings render beneath all colored lines:
    // interleaving (casing,line,casing,line,...) makes a later reach's
    // dark casing draw on top of an earlier reach's colored line wherever
    // they overlap, which at state-wide zoom turns the whole web dark.
    for(let i=0;i<visible.length;i++)if(visible[i]._mfCasing)group.addLayer(visible[i]._mfCasing);
    for(let i=0;i<visible.length;i++)group.addLayer(visible[i]);
    for(let i=0;i<visible.length;i++)if(visible[i]._mfHit)group.addLayer(visible[i]._mfHit);
    if(countEl)countEl.textContent=visible.length+' reach'+(visible.length===1?'':'es');
    if(firstPaint){
      firstPaint=false;
      // Bounds union: include gauge markers in the initial fit when
      // the layer is visible, so a state-wide view shows both reach
      // network and the gauges that monitor it (plan §2c.4).
      const boundsLayers=visible.slice();
      if(hasGaugeLayer&&showGauges){
        for(let i=0;i<gaugeMarkers.length;i++)boundsLayers.push(gaugeMarkers[i]);
      }
      if(boundsLayers.length){
        map.fitBounds(L.featureGroup(boundsLayers).getBounds().pad(0.05));
      }else{
        map.setView(DEFAULT_VIEW,DEFAULT_ZOOM);
      }
    }
    // bringToFront must run *after* fitBounds. On the very first refilter
    // the map isn't yet "loaded" (no view set), so each map.addLayer call
    // defers the layer's onAdd until the 'load' event — which only fires
    // inside fitBounds. The deferred onAdds then run in stamp-ID order,
    // and L.geoJSON stamped every line layer during construction (IDs
    // 1..N), so the lines get their SVG paths appended *before* the
    // casings and hits. That leaves casings rendered on top of lines and
    // makes chartreuse + 0.75-black blend to ~rgb(30,64,1) — exactly the
    // forest-green look. Running bringToFront after fitBounds guarantees
    // the paths exist before we re-append them in the right z-order.
    for(let i=0;i<visible.length;i++)visible[i].bringToFront();
    for(let i=0;i<visible.length;i++)if(visible[i]._mfHit)visible[i]._mfHit.bringToFront();
    writeHash(sSet,cSet,showGauges);
  }

  // Layer toggle: add/remove the gauge layerGroup without re-running
  // refilter — fitBounds is firstPaint-only, so toggling shouldn't
  // jolt the user's current pan/zoom. URL hash updates so the toggle
  // state is shareable.
  function onGaugeToggle(checked){
    showGauges=checked;
    if(showGauges){
      if(!map.hasLayer(gaugeLayer))gaugeLayer.addTo(map);
    }else if(map.hasLayer(gaugeLayer)){
      map.removeLayer(gaugeLayer);
    }
    writeHash(sSet,cSet,showGauges);
  }

  countEl=addFilterControl(sSet,cSet,refilter,hasGaugeLayer,showGauges,onGaugeToggle);
  refilter();
}

function addFilterControl(sSet,cSet,onChange,hasGaugeLayer,showGauges,onLayerToggle){
  const ctl=L.control({position:'topright'});
  let countEl;
  ctl.onAdd=function(){
    const wrap=L.DomUtil.create('div','');
    const toggle=L.DomUtil.create('button','map-filter-toggle',wrap);
    toggle.type='button';
    toggle.textContent='Filters';
    toggle.setAttribute('aria-expanded','false');

    const panel=L.DomUtil.create('div','map-filter',wrap);
    panel.setAttribute('role','region');
    panel.setAttribute('aria-label','Map filters');

    const sFs=L.DomUtil.create('fieldset','',panel);
    L.DomUtil.create('legend','',sFs).textContent='Status';
    STATUSES.forEach(function(s){
      const lab=L.DomUtil.create('label','',sFs);
      const cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.value=s;cb.checked=sSet.has(s);
      cb.addEventListener('change',function(){
        if(cb.checked)sSet.add(s);else sSet.delete(s);
        onChange();
      });
      const sw=L.DomUtil.create('span','swatch',lab);
      sw.style.background=COLORS[s];
      lab.appendChild(document.createTextNode(s.charAt(0).toUpperCase()+s.slice(1)));
    });

    const cFs=L.DomUtil.create('fieldset','',panel);
    L.DomUtil.create('legend','',cFs).textContent='Class';
    CLASS_TIERS.forEach(function(t){
      const lab=L.DomUtil.create('label','',cFs);
      const cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.value=t;cb.checked=cSet.has(t);
      cb.addEventListener('change',function(){
        if(cb.checked)cSet.add(t);else cSet.delete(t);
        onChange();
      });
      lab.appendChild(document.createTextNode(' '+t));
    });

    // Layers fieldset (Item 2c.5): only rendered when both gauge JSON
    // files loaded. Default-ON state lives in renderMap; this
    // checkbox is a thin view onto onLayerToggle.
    if(hasGaugeLayer){
      const lFs=L.DomUtil.create('fieldset','',panel);
      L.DomUtil.create('legend','',lFs).textContent='Layers';
      const lab=L.DomUtil.create('label','',lFs);
      const cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.value='gauges';cb.checked=showGauges;
      cb.addEventListener('change',function(){onLayerToggle(cb.checked);});
      lab.appendChild(document.createTextNode(' Show gauges'));
    }

    countEl=L.DomUtil.create('div','mf-count',panel);
    countEl.setAttribute('aria-live','polite');

    toggle.addEventListener('click',function(){
      const open=panel.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded',open?'true':'false');
    });

    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.disableScrollPropagation(wrap);
    return wrap;
  };
  ctl.addTo(map);
  return countEl;
}
})();
