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
// Tuned for visibility across topo + satellite + street basemaps:
//   low   #ff9800 — true Material orange (amber #e8a735 blended into
//                   topo's tans and satellite's soil tones).
//   okay  #00c853 — vivid green A700 (Material green 500 #4caf50 read
//                   as a muted forest tone over green vegetation).
//   high  #e53935 — Material red 500, no change.
//   unkn  #2196f3 — Material blue 500, no change.
var COLORS={low:'#ff9800',okay:'#00c853',high:'#e53935',unknown:'#2196f3'};
var STATUSES=['low','okay','high','unknown'];
var CLASS_TIERS=['I','II','III','IV','V','?'];
var DEFAULT_VIEW=[44.0,-120.5];
var DEFAULT_ZOOM=7;

function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}

// reaches-state.json was once {id: "status"}; it's now {id: {s, t, v, u, d, ts}}.
// Tolerate both during the cache-overlap window where an old map.js may meet
// new state JSON or vice versa.
function readEntry(state,id){
  var v=state[id];
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
  var arrow=d>0?'↑':'↓';
  var mag=t==='flow'
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

var STALE_MS=24*3600*1000;

function parseHash(){
  var out={s:null,c:null};
  var h=(location.hash||'').replace(/^#/,'');
  if(!h)return out;
  h.split('&').forEach(function(kv){
    var eq=kv.indexOf('=');
    if(eq<0)return;
    var k=kv.slice(0,eq), v=kv.slice(eq+1);
    if(k==='s'||k==='c'){
      out[k]=v===''?[]:decodeURIComponent(v).split(',').filter(Boolean);
    }
  });
  return out;
}

function writeHash(sSet,cSet){
  var parts=[];
  if(sSet.size!==STATUSES.length)parts.push('s='+Array.from(sSet).join(','));
  if(cSet.size!==CLASS_TIERS.length)parts.push('c='+Array.from(cSet).join(','));
  var hash=parts.length?('#'+parts.join('&')):'';
  if(hash!==location.hash){
    history.replaceState(null,'',location.pathname+location.search+hash);
  }
}

var mapEl=document.getElementById('map');
var geomUrl=mapEl.dataset.geomUrl;
var stateUrl=mapEl.dataset.stateUrl;

var map=L.map('map');
var topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{maxZoom:17,attribution:'OpenTopoMap'});
var street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'OpenStreetMap'});
var sat=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:18,attribution:'Esri'});
street.addTo(map);
L.control.layers({Topo:topo,Street:street,Satellite:sat}).addTo(map);

// All overlays render into the default overlayPane in a single SVG.
// iPhone Safari silently dropped overlays when they were split across
// custom panes (iPad sends a desktop UA and tolerated it).

function fail(msg){
  map.setView(DEFAULT_VIEW,DEFAULT_ZOOM);
  var ctl=L.control({position:'topright'});
  ctl.onAdd=function(){var d=L.DomUtil.create('div','map-filter');d.innerHTML='<div class="mf-err">'+esc(msg)+'</div>';return d;};
  ctl.addTo(map);
}

Promise.all([
  fetch(geomUrl).then(function(r){if(!r.ok)throw new Error('geom '+r.status);return r.json();}),
  fetch(stateUrl).then(function(r){if(!r.ok)throw new Error('state '+r.status);return r.json();}),
]).then(function(res){
  var geom=res[0], state=res[1];
  renderMap(geom,state);
}).catch(function(e){
  console.error('map data load failed:',e);
  fail('Map data failed to load.');
});

function renderMap(geom,state){
  var initial=parseHash();
  var sSet=new Set(initial.s===null?STATUSES:initial.s);
  var cSet=new Set(initial.c===null?CLASS_TIERS:initial.c);

  // Dark halo casing 2px wider than the colored line — that 1px-per-side
  // halo is what gives the orange and green lines a real outline against
  // pale topo and busy satellite. Opacity 0.4 because the halo is thinner
  // than weight alone suggests once anti-aliasing is accounted for.
  var REST_LINE={weight:4,opacity:1.0};
  var HOVER_LINE={weight:7,opacity:1.0};
  var REST_CASING={color:'#1a1a1a',weight:6,opacity:0.4,lineJoin:'round',lineCap:'round',interactive:false};
  var HOVER_CASING={weight:10};
  var HIT_LINE={weight:18,opacity:0,interactive:true,lineCap:'round',lineJoin:'round'};
  var HIT_POINT={radius:14,opacity:0,fillOpacity:0,interactive:true};

  var layersById={};
  L.geoJSON(geom,{
    style:function(f){
      var s=readEntry(state,f.properties.id).s||'unknown';
      return {color:COLORS[s]||COLORS.unknown,weight:REST_LINE.weight,opacity:REST_LINE.opacity,lineJoin:'round',lineCap:'round'};
    },
    pointToLayer:function(f,ll){
      var s=readEntry(state,f.properties.id).s||'unknown';
      return L.circleMarker(ll,{radius:6,fillColor:COLORS[s]||COLORS.unknown,color:'#333',weight:1,fillOpacity:0.8});
    },
    onEachFeature:function(f,layer){
      var p=f.properties;
      var entry=readEntry(state,p.id);
      var s=entry.s||'unknown';
      var tiers=p.tiers||['?'];
      var classDisplay=tiers.join(' · ');
      // Popup HTML is built lazily via a Leaflet popup-content function
      // so the "X hr ago" text reflects the moment the user opens it,
      // not the page-load time. Closes over p+entry; entry is the
      // snapshot captured at fetch time, which is what we want — the
      // value doesn't update without a refresh either.
      function buildPopup(){
        var dotColor=COLORS[s]||COLORS.unknown;
        var html=
          '<a class="reach-popup" href="/description.php?id='+parseInt(p.id,10)+'">'+
            '<div class="rp-name">'+esc(p.name)+'</div>';
        var ageStr='';
        if('v' in entry){
          var val=fmtValue(entry.v,entry.t,entry.u);
          var delta=fmtDelta(entry.d,entry.t,entry.u);
          var ageMs=entry.ts?(Date.now()-Date.parse(entry.ts)):-1;
          ageStr=ageMs>=0?fmtAge(ageMs):'';
          var stale=ageMs>STALE_MS;
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
      var hit=null;
      if(typeof layer.getLatLngs==='function'){
        hit=L.polyline(layer.getLatLngs(),HIT_LINE);
      }else if(typeof layer.getLatLng==='function'){
        hit=L.circleMarker(layer.getLatLng(),HIT_POINT);
      }
      layer._mfHit=hit;

      var target=hit||layer;
      target.bindPopup(buildPopup);
      target.on('mouseover',function(){
        layer.setStyle(HOVER_LINE);
        if(layer._mfCasing)layer._mfCasing.setStyle(HOVER_CASING);
      });
      target.on('mouseout',function(){
        layer.setStyle(REST_LINE);
        if(layer._mfCasing)layer._mfCasing.setStyle({weight:REST_CASING.weight});
      });
    },
  });

  var group=L.layerGroup().addTo(map);

  function matches(layer){
    if(!sSet.has(layer._mfStatus))return false;
    var tiers=layer._mfTiers;
    for(var i=0;i<tiers.length;i++)if(cSet.has(tiers[i]))return true;
    return false;
  }

  var countEl=null;
  var firstPaint=true;
  function refilter(){
    group.clearLayers();
    var visible=[];
    for(var id in layersById){
      if(matches(layersById[id]))visible.push(layersById[id]);
    }
    // Three passes so all casings render beneath all colored lines:
    // interleaving (casing,line,casing,line,...) makes a later reach's
    // dark casing draw on top of an earlier reach's colored line wherever
    // they overlap, which at state-wide zoom turns the whole web dark.
    for(var i=0;i<visible.length;i++)if(visible[i]._mfCasing)group.addLayer(visible[i]._mfCasing);
    for(i=0;i<visible.length;i++)group.addLayer(visible[i]);
    for(i=0;i<visible.length;i++)if(visible[i]._mfHit)group.addLayer(visible[i]._mfHit);
    if(countEl)countEl.textContent=visible.length+' reach'+(visible.length===1?'':'es');
    if(firstPaint){
      firstPaint=false;
      if(visible.length){
        map.fitBounds(L.featureGroup(visible).getBounds().pad(0.05));
      }else{
        map.setView(DEFAULT_VIEW,DEFAULT_ZOOM);
      }
    }
    writeHash(sSet,cSet);
  }

  countEl=addFilterControl(sSet,cSet,refilter);
  refilter();
}

function addFilterControl(sSet,cSet,onChange){
  var ctl=L.control({position:'topright'});
  var countEl;
  ctl.onAdd=function(){
    var wrap=L.DomUtil.create('div','');
    var toggle=L.DomUtil.create('button','map-filter-toggle',wrap);
    toggle.type='button';
    toggle.textContent='Filters';
    toggle.setAttribute('aria-expanded','false');

    var panel=L.DomUtil.create('div','map-filter',wrap);
    panel.setAttribute('role','region');
    panel.setAttribute('aria-label','Map filters');

    var sFs=L.DomUtil.create('fieldset','',panel);
    L.DomUtil.create('legend','',sFs).textContent='Status';
    STATUSES.forEach(function(s){
      var lab=L.DomUtil.create('label','',sFs);
      var cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.value=s;cb.checked=sSet.has(s);
      cb.addEventListener('change',function(){
        if(cb.checked)sSet.add(s);else sSet.delete(s);
        onChange();
      });
      var sw=L.DomUtil.create('span','swatch',lab);
      sw.style.background=COLORS[s];
      lab.appendChild(document.createTextNode(s.charAt(0).toUpperCase()+s.slice(1)));
    });

    var cFs=L.DomUtil.create('fieldset','',panel);
    L.DomUtil.create('legend','',cFs).textContent='Class';
    CLASS_TIERS.forEach(function(t){
      var lab=L.DomUtil.create('label','',cFs);
      var cb=L.DomUtil.create('input','',lab);
      cb.type='checkbox';cb.value=t;cb.checked=cSet.has(t);
      cb.addEventListener('change',function(){
        if(cb.checked)cSet.add(t);else cSet.delete(t);
        onChange();
      });
      lab.appendChild(document.createTextNode(' '+t));
    });

    countEl=L.DomUtil.create('div','mf-count',panel);
    countEl.setAttribute('aria-live','polite');

    toggle.addEventListener('click',function(){
      var open=panel.classList.toggle('is-open');
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
