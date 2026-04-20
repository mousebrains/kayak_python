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
var COLORS={low:'#e8a735',okay:'#4caf50',high:'#e53935',unknown:'#2196F3'};
var STATUSES=['low','okay','high','unknown'];
var CLASS_TIERS=['I','II','III','IV','V','?'];
var DEFAULT_VIEW=[44.0,-120.5];
var DEFAULT_ZOOM=7;

function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}

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

  var layersById={};
  L.geoJSON(geom,{
    style:function(f){
      var s=state[f.properties.id]||'unknown';
      return {color:COLORS[s]||COLORS.unknown,weight:3,opacity:0.7};
    },
    pointToLayer:function(f,ll){
      var s=state[f.properties.id]||'unknown';
      return L.circleMarker(ll,{radius:6,fillColor:COLORS[s]||COLORS.unknown,color:'#333',weight:1,fillOpacity:0.8});
    },
    onEachFeature:function(f,layer){
      var p=f.properties;
      var s=state[p.id]||'unknown';
      var badge='<span style="color:'+(COLORS[s]||COLORS.unknown)+'">&#9679;</span> '+esc(s);
      layer.bindPopup('<b><a href="/description.php?id='+parseInt(p.id,10)+'">'+esc(p.name)+'</a></b><br>'+badge);
      layersById[p.id]=layer;
      layer._mfStatus=s;
      layer._mfTiers=p.tiers||['?'];
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
      var l=layersById[id];
      if(matches(l)){group.addLayer(l);visible.push(l);}
    }
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
