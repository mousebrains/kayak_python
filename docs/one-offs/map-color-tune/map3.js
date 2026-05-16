/* Three-up trace color comparison with live controls.
 *
 * Renders the same reach set on three base maps and mirrors pan/zoom
 * between them. A control bar drives the palette, line weight, casing
 * strategy, optional per-status dash, hover-sync (highlight a reach on
 * all three maps at once), zoom-aware line weight, and a per-basemap
 * casing override that hides the dark halo on OSM (whose white-ish
 * background already provides contrast). All settings round-trip
 * through the URL hash so a chosen variant is shareable.
 */
(function(){

// ---- palette & casing variants -------------------------------------------

var PALETTES={
  current:{low:'#ff9800',okay:'#00c853',high:'#e53935',unknown:'#2196f3'},
  v2:     {low:'#ff6d00',okay:'#76ff03',high:'#ff1744',unknown:'#00b0ff'},
  v3:     {low:'#ff6d00',okay:'#aeea00',high:'#d50000',unknown:'#00e5ff'},
  bold:   {low:'#ff6d00',okay:'#76ff03',high:'#ff1744',unknown:'#d500f9'},
};

var CASING_MODES={
  none:   null,
  light:  {color:'#000',extra:2,opacity:0.50},
  strong: {color:'#000',extra:2,opacity:0.75},
  double: {color:'#000',extra:4,opacity:0.85},
};
var INNER_CASING={color:'#fff',extra:2,opacity:0.85};

var BASES={
  topo:  {url:'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
          opts:{maxZoom:17,attribution:'OpenTopoMap'}},
  street:{url:'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
          opts:{maxZoom:19,attribution:'OpenStreetMap'}},
  sat:   {url:'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          opts:{maxZoom:18,attribution:'Esri'}},
};

var DEFAULT_VIEW=[44.0,-120.5];
var DEFAULT_ZOOM=7;
var DASH_PATTERN='8,6';
var HOVER_BUMP=3;

// ---- state ---------------------------------------------------------------

var settings={
  palette:'current',
  weight:4,
  casing:'light',
  hover:false,
  zoomAware:false,
  dashed:'none',
  hideOsmCasing:false,
};

var mapObjs=null;
var reachIndex={};
var hoveredId=null;
var restyleScheduled=false;

function readEntry(state,id){
  var v=state[id];
  if(typeof v==='string')return {s:v};
  return v||{s:'unknown'};
}

// ---- style helpers -------------------------------------------------------

function effectiveWeight(){
  var w=settings.weight;
  if(!settings.zoomAware||!mapObjs)return w;
  var z=mapObjs[0].map.getZoom();
  if(z>=11)return w+2;
  if(z>=9)return w+1;
  return w;
}

function styleForReach(r,w){
  var p=PALETTES[settings.palette];
  var color=p[r.status]||p.unknown;
  var dash=(settings.dashed!=='none'&&settings.dashed===r.status)?DASH_PATTERN:null;

  var line={color:color,weight:w,opacity:1.0,lineJoin:'round',lineCap:'round',dashArray:dash};

  var outer=null;
  var cm=CASING_MODES[settings.casing];
  if(cm){
    outer={color:cm.color,weight:w+cm.extra,opacity:cm.opacity,lineJoin:'round',lineCap:'round',interactive:false,dashArray:dash};
  }

  var inner=null;
  if(settings.casing==='double'){
    inner={color:INNER_CASING.color,weight:w+INNER_CASING.extra,opacity:INNER_CASING.opacity,lineJoin:'round',lineCap:'round',interactive:false,dashArray:dash};
  }

  return {line:line,outer:outer,inner:inner};
}

// ---- map building --------------------------------------------------------

function buildMap(divId,baseKey,geom,state){
  var map=L.map(divId,{zoomControl:true,attributionControl:true});
  var b=BASES[baseKey];
  L.tileLayer(b.url,b.opts).addTo(map);

  // Each reach gets four polylines up-front: outer + inner casings, the
  // colored line, and an invisible wide "hit" polyline so finger/mouse
  // hover lands on something even at line weight 3. The hit polyline's
  // handler gates on settings.hover, so it costs nothing when off.
  var reaches=[];
  L.geoJSON(geom,{
    onEachFeature:function(f,layer){
      if(typeof layer.getLatLngs!=='function')return;
      var coords=layer.getLatLngs();
      var status=readEntry(state,f.properties.id).s||'unknown';
      reaches.push({
        id:f.properties.id,
        status:status,
        line: L.polyline(coords,{weight:1,opacity:0}),
        outer:L.polyline(coords,{weight:1,opacity:0,interactive:false}),
        inner:L.polyline(coords,{weight:1,opacity:0,interactive:false}),
        hit:  L.polyline(coords,{weight:18,opacity:0,interactive:true,lineCap:'round',lineJoin:'round'}),
      });
    },
  });

  return {baseKey:baseKey,map:map,group:L.layerGroup().addTo(map),reaches:reaches};
}

function buildReachIndex(){
  var idx={};
  mapObjs.forEach(function(m){
    m.reaches.forEach(function(r){
      if(!idx[r.id])idx[r.id]=[];
      idx[r.id].push(r);
    });
  });
  return idx;
}

function applyStyles(){
  var w=effectiveWeight();
  mapObjs.forEach(function(m){
    var hideCasing=settings.hideOsmCasing&&m.baseKey==='street';
    m.group.clearLayers();

    // Three-pass add (outer → inner → line) keeps every casing under
    // every colored line. Interleaving would let a later reach's casing
    // draw over an earlier reach's colored line wherever they cross.
    var styled=m.reaches.map(function(r){return {r:r,s:styleForReach(r,w)};});

    if(!hideCasing){
      styled.forEach(function(p){
        if(p.s.outer){p.r.outer.setStyle(p.s.outer);m.group.addLayer(p.r.outer);}
      });
      if(settings.casing==='double'){
        styled.forEach(function(p){
          if(p.s.inner){p.r.inner.setStyle(p.s.inner);m.group.addLayer(p.r.inner);}
        });
      }
    }
    styled.forEach(function(p){p.r.line.setStyle(p.s.line);m.group.addLayer(p.r.line);});
    styled.forEach(function(p){m.group.addLayer(p.r.hit);});
  });

  // Re-apply any in-flight hover thickening — settings changes shouldn't
  // strand the currently-hovered reach at base weight.
  if(hoveredId!=null)setHover(hoveredId,true);
}

function applyLegend(){
  var p=PALETTES[settings.palette];
  var root=document.documentElement.style;
  root.setProperty('--col-low',p.low);
  root.setProperty('--col-okay',p.okay);
  root.setProperty('--col-high',p.high);
  root.setProperty('--col-unknown',p.unknown);
}

function setHover(id,on){
  var reaches=reachIndex[id]||[];
  var w=effectiveWeight()+(on?HOVER_BUMP:0);
  reaches.forEach(function(r){
    var s=styleForReach(r,w);
    r.line.setStyle(s.line);
    if(s.outer)r.outer.setStyle(s.outer);
    if(s.inner)r.inner.setStyle(s.inner);
  });
}

function bindHover(){
  mapObjs.forEach(function(m){
    m.reaches.forEach(function(r){
      r.hit.on('mouseover',function(){
        if(!settings.hover)return;
        hoveredId=r.id;
        setHover(r.id,true);
      });
      r.hit.on('mouseout',function(){
        if(!settings.hover)return;
        if(hoveredId===r.id)hoveredId=null;
        setHover(r.id,false);
      });
    });
  });
}

function syncMaps(maps){
  var syncing=false;
  maps.forEach(function(m){
    m.on('move zoom',function(){
      if(syncing)return;
      syncing=true;
      var c=m.getCenter(),z=m.getZoom();
      for(var i=0;i<maps.length;i++){
        if(maps[i]!==m)maps[i].setView(c,z,{animate:false});
      }
      syncing=false;
    });
  });
}

function scheduleRestyle(){
  if(restyleScheduled)return;
  restyleScheduled=true;
  requestAnimationFrame(function(){restyleScheduled=false;applyStyles();});
}

// ---- hash round-trip -----------------------------------------------------

var DASH_OPTS={none:1,low:1,okay:1,high:1,unknown:1};

function parseHash(){
  var h=(location.hash||'').replace(/^#/,'');
  if(!h)return;
  var got={};
  h.split('&').forEach(function(kv){
    var i=kv.indexOf('=');
    if(i<0)return;
    got[kv.slice(0,i)]=decodeURIComponent(kv.slice(i+1));
  });
  if(got.p&&PALETTES[got.p])settings.palette=got.p;
  if(got.w){var w=parseInt(got.w,10);if(w>=3&&w<=7)settings.weight=w;}
  if(got.c&&(got.c in CASING_MODES))settings.casing=got.c;
  if(got.h==='1')settings.hover=true;
  if(got.z==='1')settings.zoomAware=true;
  if(got.d&&DASH_OPTS[got.d])settings.dashed=got.d;
  if(got.o==='1')settings.hideOsmCasing=true;
}

function writeHash(){
  var parts=[];
  if(settings.palette!=='current')parts.push('p='+settings.palette);
  if(settings.weight!==4)parts.push('w='+settings.weight);
  if(settings.casing!=='light')parts.push('c='+settings.casing);
  if(settings.hover)parts.push('h=1');
  if(settings.zoomAware)parts.push('z=1');
  if(settings.dashed!=='none')parts.push('d='+settings.dashed);
  if(settings.hideOsmCasing)parts.push('o=1');
  var hash=parts.length?'#'+parts.join('&'):'';
  if(hash!==location.hash){
    history.replaceState(null,'',location.pathname+location.search+hash);
  }
}

// ---- form binding --------------------------------------------------------

function bindControls(onChange){
  var form=document.getElementById('controls');
  var weightOut=document.getElementById('weight-out');

  function readForm(){
    settings.palette=form.palette.value;
    settings.weight=parseInt(form.weight.value,10);
    settings.casing=form.casing.value;
    settings.hover=form.hover.checked;
    settings.zoomAware=form.zoomAware.checked;
    settings.dashed=form.dashed.value;
    settings.hideOsmCasing=form.hideOsmCasing.checked;
    weightOut.value=settings.weight;
  }

  function writeForm(){
    form.palette.value=settings.palette;
    form.weight.value=String(settings.weight);
    form.casing.value=settings.casing;
    form.hover.checked=settings.hover;
    form.zoomAware.checked=settings.zoomAware;
    form.dashed.value=settings.dashed;
    form.hideOsmCasing.checked=settings.hideOsmCasing;
    weightOut.value=settings.weight;
  }

  function onAny(){readForm();onChange();}
  form.addEventListener('input',onAny);
  form.addEventListener('change',onAny);

  return writeForm;
}

// ---- bootstrap -----------------------------------------------------------

function fail(msg){
  document.querySelectorAll('.map').forEach(function(el){
    el.innerHTML='<div style="padding:1rem;color:#f88">'+msg+'</div>';
  });
}

parseHash();

var geomUrl=document.body.dataset.geomUrl+'?v='+Math.floor(Date.now()/3600000);
var stateUrl=document.body.dataset.stateUrl;

Promise.all([
  fetch(geomUrl).then(function(r){if(!r.ok)throw new Error('geom '+r.status);return r.json();}),
  fetch(stateUrl).then(function(r){if(!r.ok)throw new Error('state '+r.status);return r.json();}),
]).then(function(res){
  var geom=res[0],state=res[1];
  mapObjs=[
    buildMap('map-topo','topo',geom,state),
    buildMap('map-street','street',geom,state),
    buildMap('map-sat','sat',geom,state),
  ];
  reachIndex=buildReachIndex();
  bindHover();

  var maps=mapObjs.map(function(m){return m.map;});
  maps.forEach(function(m){m.setView(DEFAULT_VIEW,DEFAULT_ZOOM);});
  syncMaps(maps);

  maps.forEach(function(m){
    m.on('zoomend',function(){if(settings.zoomAware)scheduleRestyle();});
  });

  function refresh(){applyLegend();applyStyles();writeHash();}
  var syncForm=bindControls(refresh);
  syncForm();
  refresh();

  window.addEventListener('resize',function(){
    maps.forEach(function(m){m.invalidateSize();});
  });
}).catch(function(e){
  console.error('map data load failed:',e);
  fail('Map data failed to load.');
});

})();
