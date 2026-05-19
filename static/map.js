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

// Item 1 of docs/done/PLAN_map_and_ui_tweaks.md: hover-opens-popup is desktop-
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

// Item 2 of docs/done/PLAN_map_and_ui_tweaks.md — gauge markers.
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

// Oregon SMB overlay markers. Colors chosen to NOT collide with the
// reach palette (orange/chartreuse/red/cyan) or the gauge color (which
// reuses the reach palette): magenta for hazards, deep purple for
// dams, dark green for access.
//
// Shape distinguishes hazard tier at a glance — triangle (warning)
// for obstructions sits above diamond (fixed structure) for dams sits
// above circle (info) for access + gauges. Sizes step down to match:
// 16 > 14 > 10 (diameter) > 6–14 (gauges, zoom-graded).
//
// Z-order top → bottom (per user request):
//   1. obstructions  (markerPane, zIndexOffset 200)
//   2. dams / weirs  (markerPane, zIndexOffset 100)
//   3. access sites  (overlayPane, canvas — appends after SVG)
//   4. gauges        (overlayPane, SVG — bringToFront after reaches)
//   5. reaches       (overlayPane, SVG — bottom)
// markerPane is a built-in pane stacked above overlayPane, so dams +
// obstructions clear everything else regardless of add order.
const OSMB_HIT_RADIUS=14;
// Popup function refs are forward references — they're function
// declarations later in this IIFE, which JS hoists to module top.
const OSMB_LAYER_DEFS=[
  {key:'obstructions',label:'Obstructions',color:'#ff00ff',defaultOn:false,attr:'osmbObstructionsUrl',popup:obstructionPopup,shape:'triangle',size:16,zIndex:200},
  {key:'dams',        label:'Dams / weirs',color:'#6a1b9a',defaultOn:false,attr:'osmbDamsUrl',        popup:damPopup,        shape:'diamond', size:14,zIndex:100},
  {key:'access',      label:'Access sites',color:'#1b5e20',defaultOn:false,attr:'osmbAccessUrl',      popup:accessPopup,     shape:'circle',  size:5, zIndex:0  },
];

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
  // so the common-case URL stays short. OSMB overlays follow the same
  // rule, with each layer's default coming from OSMB_LAYER_DEFS.
  const out={s:null,c:null,gauges:true,osmb:{}};
  OSMB_LAYER_DEFS.forEach(function(d){out.osmb[d.key]=d.defaultOn;});
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
    }else if(k in out.osmb){
      out.osmb[k]=(v==='on');
    }
  });
  return out;
}

function writeHash(sSet,cSet,showGauges,osmbVisible){
  const parts=[];
  if(sSet.size!==STATUSES.length)parts.push('s='+Array.from(sSet).join(','));
  if(cSet.size!==CLASS_TIERS.length)parts.push('c='+Array.from(cSet).join(','));
  if(showGauges===false)parts.push('gauges=off');
  if(osmbVisible){
    OSMB_LAYER_DEFS.forEach(function(d){
      const on=osmbVisible[d.key];
      if(on!==d.defaultOn)parts.push(d.key+'='+(on?'on':'off'));
    });
  }
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
// OSMB overlay URLs — empty until the nightly fetcher has landed a file.
// Empty-string handling: fetchOptional short-circuits to null.
const osmbUrls=OSMB_LAYER_DEFS.map(function(d){return mapEl.dataset[d.attr]||'';});

const map=L.map('map');
const topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{maxZoom:17,attribution:'OpenTopoMap'});
const street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'OpenStreetMap'});
const sat=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,attribution:'Esri'});
street.addTo(map);
L.control.scale({imperial:true,metric:false}).addTo(map);
// Base tiles are constant; overlay layers (gauges + OSMB) are built
// inside renderMap once their JSON loads, so the combined layer
// control is constructed there too. BASE_LAYERS gets passed in.
const BASE_LAYERS={Topo:topo,Street:street,Satellite:sat};

// All overlays render into the default overlayPane in a single SVG.
// iPhone Safari silently dropped overlays when they were split across
// custom panes (iPad sends a desktop UA and tolerated it).

function fail(msg){
  map.setView(DEFAULT_VIEW,DEFAULT_ZOOM);
  const ctl=L.control({position:'topright'});
  ctl.onAdd=function(){
    const d=L.DomUtil.create('div','map-error');
    d.textContent=msg;
    return d;
  };
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
  Promise.all(OSMB_LAYER_DEFS.map(function(d,i){
    return fetchOptional(osmbUrls[i],'osmb-'+d.key);
  })),
]).then(function(res){
  renderMap(res[0],res[1],res[2],res[3],res[4]);
}).catch(function(e){
  console.error('map data load failed:',e);
  fail('Map data failed to load.');
});

function renderMap(geom,state,gaugesGeom,gaugesState,osmbData){
  const initial=parseHash();
  const sSet=new Set(initial.s===null?STATUSES:initial.s);
  const cSet=new Set(initial.c===null?CLASS_TIERS:initial.c);
  // Gauge layer is available only when both files loaded cleanly.
  // hasGaugeLayer gates the filter checkbox AND the fitBounds union.
  const hasGaugeLayer = !!(gaugesGeom && gaugesState);
  let showGauges = hasGaugeLayer && initial.gauges;
  const osmbLayers={};
  const osmbVisible={};
  OSMB_LAYER_DEFS.forEach(function(d,i){
    const data=osmbData?osmbData[i]:null;
    if(!data)return;
    osmbLayers[d.key]=buildOsmbLayer(data,d);
    osmbVisible[d.key]=initial.osmb[d.key];
    if(osmbVisible[d.key])osmbLayers[d.key].addTo(map);
  });

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
      // docs/done/PLAN_map_and_ui_tweaks.md.
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
      // Desktop click-to-navigate: hover already shows the preview
      // popup, so a click on the trace should commit to the
      // description page rather than toggle the popup (Leaflet's
      // default click behavior). Touch devices keep tap-to-open —
      // the popup body is itself an <a href> for the second tap.
      if(DESKTOP_HOVER){
        target.on('click',function(){
          window.location.href='/description.php?id='+parseInt(p.id,10);
        });
      }
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
      // Desktop click-to-navigate: same rationale as the reach path
      // above. Hover already shows the gauge preview popup; a click
      // commits to /gauge.php instead of toggling the popup closed.
      if(DESKTOP_HOVER){
        hit.on('click',function(){
          window.location.href='/gauge.php?id='+parseInt(gid,10);
        });
      }
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
    applyZOrder();
    writeHash(sSet,cSet,showGauges,osmbVisible);
  }

  // Enforce within-SVG stacking: gauges above reaches. Reach paths get
  // rebuilt every refilter() (status/class filter change), which appends
  // them after the existing gauge paths in the SVG — so gauges sink
  // below. Calling bringToFront on each visible gauge here re-appends
  // each gauge marker as the last SVG child, restoring the desired
  // order. Dams + obstructions live in markerPane (a built-in pane
  // stacked above overlayPane) with zIndexOffset set at marker
  // creation; access uses canvas in overlayPane and naturally appends
  // above the SVG. So this function only has to handle gauges.
  function applyZOrder(){
    if(!hasGaugeLayer||!showGauges)return;
    for(let i=0;i<gaugeMarkers.length;i++){
      const m=gaugeMarkers[i];
      if(typeof m.bringToFront==='function')m.bringToFront();
    }
  }

  // Base map + overlay toggles live in the unified filter panel (built by
  // addFilterControl). The panel owns its own state via these callbacks —
  // we mutate outer-scope showGauges / osmbVisible and feed writeHash so
  // the URL hash stays in sync.
  function onBaseChange(name){
    for(const k in BASE_LAYERS){
      if(k!==name && map.hasLayer(BASE_LAYERS[k]))map.removeLayer(BASE_LAYERS[k]);
    }
    BASE_LAYERS[name].addTo(map);
  }
  function onOverlayToggle(key,on){
    if(key==='gauges'){
      showGauges=on;
      if(on)gaugeLayer.addTo(map);else map.removeLayer(gaugeLayer);
    }else{
      osmbVisible[key]=on;
      const lyr=osmbLayers[key];
      if(!lyr)return;
      if(on)lyr.addTo(map);else map.removeLayer(lyr);
    }
    applyZOrder();
    writeHash(sSet,cSet,showGauges,osmbVisible);
  }

  countEl=addFilterControl(sSet,cSet,refilter,{
    baseLayers:BASE_LAYERS,
    currentBase:'Street',
    onBaseChange:onBaseChange,
    hasGaugeLayer:hasGaugeLayer,
    showGauges:showGauges,
    osmbLayers:osmbLayers,
    osmbVisible:osmbVisible,
    onOverlayToggle:onOverlayToggle,
  });
  refilter();
}

// Canvas renderer for the high-count access layer (1816 markers — SVG
// would push the page past 3600 path elements). Obstructions + dams
// (194 + 163) use L.marker + L.divIcon SVG: low enough counts that SVG
// perf is fine, and L.marker auto-places them in markerPane (above
// overlayPane) for the z-order the user requested.
const OSMB_CANVAS_RENDERER=L.canvas();

function buildOsmbLayer(data,def){
  const group=L.layerGroup();
  const features=(data&&data.features)||[];
  for(let i=0;i<features.length;i++){
    const f=features[i];
    const coords=f.geometry&&f.geometry.coordinates;
    if(!coords||coords.length<2)continue;
    const ll=L.latLng(coords[1],coords[0]);
    const props=f.properties||{};
    if(def.shape==='circle'){
      L.circleMarker(ll,{
        renderer:OSMB_CANVAS_RENDERER,
        radius:def.size,
        fillColor:def.color,
        color:'#222',
        weight:1,
        fillOpacity:0.85,
        interactive:false,
      }).addTo(group);
      const hit=L.circleMarker(ll,{
        renderer:OSMB_CANVAS_RENDERER,
        radius:OSMB_HIT_RADIUS,
        opacity:0,
        fillOpacity:0,
        interactive:true,
      }).addTo(group);
      hit.bindPopup(def.popup.bind(null,props));
    }else{
      const marker=L.marker(ll,{
        icon:makeShapeIcon(def.shape,def.size,def.color),
        zIndexOffset:def.zIndex||0,
        keyboard:false,
      }).addTo(group);
      marker.bindPopup(def.popup.bind(null,props));
    }
  }
  return group;
}

// Build an L.divIcon with inline SVG for the non-circle OSMB markers.
// The visible shape sits inside a 28×28 hit box — matches the access
// layer's hit-circle diameter so tap targets are uniform across shapes.
// No external CSS needed: fill + stroke are SVG attrs (CSP-safe).
function makeShapeIcon(shape,size,color){
  const box=28;
  const c=box/2;
  const half=size/2;
  let pts='';
  if(shape==='triangle'){
    // Equilateral, apex up. Width = size; height = size * sin(60°) ≈ 0.866.
    const halfW=half*0.866;
    pts=c+','+(c-half)+' '+(c+halfW)+','+(c+half*0.5)+' '+(c-halfW)+','+(c+half*0.5);
  }else if(shape==='diamond'){
    pts=c+','+(c-half)+' '+(c+half)+','+c+' '+c+','+(c+half)+' '+(c-half)+','+c;
  }
  const svg='<svg width="'+box+'" height="'+box+'" viewBox="0 0 '+box+' '+box+'" xmlns="http://www.w3.org/2000/svg">'+
    '<polygon points="'+pts+'" fill="'+color+'" stroke="#222" stroke-width="1" stroke-linejoin="round"/>'+
  '</svg>';
  return L.divIcon({
    className:'osmb-icon osmb-icon--'+shape,
    html:svg,
    iconSize:[box,box],
    iconAnchor:[c,c],
    popupAnchor:[0,-half],
  });
}

// Static landing URLs for the three OSMB popups. None of the layers
// support per-feature deep-linking (the AGOL dashboards/experiences
// don't update their URL on marker click), so each popup links to the
// OSMB-hosted overview page for its layer.
const OSMB_DAM_URL='https://www.oregon.gov/osmb/boating-facilities/Pages/Maps-and-Apps.aspx';
const OSMB_OBSTRUCTION_URL='https://geo.maps.arcgis.com/apps/dashboards/59f4dfde321f447b9245a1451c83e054';
const OSMB_ACCESS_URL='https://experience.arcgis.com/experience/72308dd6b893451690a14437cde89be8';

function obstructionPopup(p){
  const title=esc(p.obslocation||p.waterbody||'Obstruction');
  const sub=[p.waterbody,p.waterbodysec].filter(Boolean).map(esc).join(' · ');
  const desc=esc(p.obsdescript||'');
  const ageMs=p.recordtime?(Date.now()-Number(p.recordtime)):-1;
  const age=ageMs>=0?'<span class="rp-time">'+esc(fmtAge(ageMs))+'</span>':'';
  let html='<a class="reach-popup" href="'+OSMB_OBSTRUCTION_URL+'" target="_blank" rel="noopener">'+
    '<div class="rp-name">'+title+'</div>';
  if(sub)html+='<div class="rp-sub">'+sub+'</div>';
  if(desc)html+='<div class="rp-reading">'+desc+'</div>';
  if(age)html+='<div class="rp-footer">'+age+'</div>';
  html+='</a>';
  return html;
}

function damPopup(p){
  const title=esc(p.damname||'Dam');
  const sub=esc(p.waterbody||'');
  const sizeBits=[];
  if(p.damheight)sizeBits.push(p.damheight+' ft tall');
  if(p.damwidth)sizeBits.push(p.damwidth+' ft wide');
  const portage=esc(p.portagedesc||p.navigate||'');
  let html='<a class="reach-popup" href="'+OSMB_DAM_URL+'" target="_blank" rel="noopener">'+
    '<div class="rp-name">'+title+'</div>';
  if(sub)html+='<div class="rp-sub">'+sub+'</div>';
  if(sizeBits.length)html+='<div class="rp-reading">'+esc(sizeBits.join(' · '))+'</div>';
  if(portage)html+='<div class="rp-sub">'+portage+'</div>';
  html+='</a>';
  return html;
}

function accessPopup(p){
  const title=esc(p.name||'Access site');
  const sub=esc(p.waterway_name||'');
  const facility=esc([p.facility_type,p.launch_type].filter(Boolean).join(' · '));
  let html='<a class="reach-popup" href="'+OSMB_ACCESS_URL+'" target="_blank" rel="noopener">'+
    '<div class="rp-name">'+title+'</div>';
  if(sub)html+='<div class="rp-sub">'+sub+'</div>';
  if(facility)html+='<div class="rp-reading">'+facility+'</div>';
  html+='</a>';
  return html;
}

function addFilterControl(sSet,cSet,onChange,opts){
  const ctl=L.control({position:'topright'});
  let countEl;
  ctl.onAdd=function(){
    const wrap=L.DomUtil.create('div','map-filter-wrap');
    const toggle=L.DomUtil.create('button','map-filter-toggle',wrap);
    toggle.type='button';
    toggle.setAttribute('aria-expanded','false');
    toggle.setAttribute('aria-label','Map layers and filters');
    const t1=L.DomUtil.create('span','mft-line',toggle);t1.textContent='Layers';
    const t2=L.DomUtil.create('span','mft-line',toggle);t2.textContent='Filters';
    const tch=L.DomUtil.create('span','mft-chevron',toggle);
    tch.setAttribute('aria-hidden','true');
    tch.textContent='▾';

    const panel=L.DomUtil.create('div','map-filter',wrap);
    panel.setAttribute('role','region');
    panel.setAttribute('aria-label','Map layers and filters');

    // Base map — single-select pill row.
    const bFs=L.DomUtil.create('fieldset','',panel);
    L.DomUtil.create('legend','',bFs).textContent='Base map';
    const bPills=L.DomUtil.create('div','filter-pills',bFs);
    Object.keys(opts.baseLayers).forEach(function(name){
      const lab=L.DomUtil.create('label','',bPills);
      const r=L.DomUtil.create('input','',lab);
      r.type='radio';r.name='map-base';r.value=name;
      r.checked=(name===opts.currentBase);
      r.addEventListener('change',function(){
        if(r.checked)opts.onBaseChange(name);
      });
      lab.appendChild(document.createTextNode(name));
    });

    // Overlays — stacked checkboxes; skip layers that failed to load.
    const ovFs=L.DomUtil.create('fieldset','mf-stacked',panel);
    L.DomUtil.create('legend','',ovFs).textContent='Overlays';
    if(opts.hasGaugeLayer){
      const lab=L.DomUtil.create('label','',ovFs);
      const cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.checked=opts.showGauges;
      cb.addEventListener('change',function(){opts.onOverlayToggle('gauges',cb.checked);});
      const sw=L.DomUtil.create('span','swatch',lab);
      sw.style.background=COLORS.unknown;
      lab.appendChild(document.createTextNode('Gauges'));
    }
    OSMB_LAYER_DEFS.forEach(function(d){
      if(!opts.osmbLayers[d.key])return;
      const lab=L.DomUtil.create('label','',ovFs);
      const cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.checked=!!opts.osmbVisible[d.key];
      cb.addEventListener('change',function(){opts.onOverlayToggle(d.key,cb.checked);});
      const sw=L.DomUtil.create('span','swatch',lab);
      sw.style.background=d.color;
      lab.appendChild(document.createTextNode(d.label));
    });

    // Status — pill row with colored swatch.
    const sFs=L.DomUtil.create('fieldset','',panel);
    L.DomUtil.create('legend','',sFs).textContent='Status';
    const sPills=L.DomUtil.create('div','filter-pills',sFs);
    STATUSES.forEach(function(s){
      const lab=L.DomUtil.create('label','',sPills);
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

    // Class — pill row, label only.
    const cFs=L.DomUtil.create('fieldset','',panel);
    L.DomUtil.create('legend','',cFs).textContent='Class';
    const cPills=L.DomUtil.create('div','filter-pills',cFs);
    CLASS_TIERS.forEach(function(t){
      const lab=L.DomUtil.create('label','',cPills);
      const cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.value=t;cb.checked=cSet.has(t);
      cb.addEventListener('change',function(){
        if(cb.checked)cSet.add(t);else cSet.delete(t);
        onChange();
      });
      lab.appendChild(document.createTextNode(t));
    });

    countEl=L.DomUtil.create('div','mf-count',panel);
    countEl.setAttribute('aria-live','polite');

    // Sticky-chevron overflow indicator — toggled via .has-overflow when
    // panel content exceeds visible height. iOS scrollbars stay hidden
    // until the user touches the panel, so this is the discoverable cue.
    function updateOverflowHint(){
      panel.classList.toggle('has-overflow', panel.scrollHeight - panel.clientHeight > 4);
    }
    if(typeof ResizeObserver==='function'){
      new ResizeObserver(updateOverflowHint).observe(panel);
    }

    toggle.addEventListener('click',function(){
      const open=panel.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded',open?'true':'false');
      if(open)requestAnimationFrame(updateOverflowHint);
    });

    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.disableScrollPropagation(wrap);
    return wrap;
  };
  ctl.addTo(map);
  return countEl;
}
})();
