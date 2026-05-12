(function(){
'use strict';
var el=document.getElementById('search-map');
if(!el)return;
try{var reaches=JSON.parse(el.dataset.reaches);var colors=JSON.parse(el.dataset.colors)}
catch{return;}
if(!reaches||!colors)return;
var gauges=[];
try{gauges=JSON.parse(el.dataset.gauges||'[]')}catch{}
var map=L.map('search-map');
var topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{
  attribution:'OpenTopoMap',maxZoom:17});
var street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
  attribution:'OpenStreetMap',maxZoom:19});
var satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
  attribution:'Esri',maxZoom:19});
topo.addTo(map);
L.control.layers({'Topo':topo,'Street':street,'Satellite':satellite}).addTo(map);
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
var bounds=[];
reaches.forEach(function(r){
  var c=colors[r.idx%colors.length];
  var markerLL=(r.track&&r.track.length>1)?r.track[Math.floor(r.track.length/2)]:[r.lat,r.lon];
  var ic=L.divIcon({className:'',html:'<div style="background:'+c+';color:#fff;padding:2px 6px;border-radius:3px;font:bold 11px sans-serif;white-space:nowrap;cursor:pointer;border:1px solid rgba(0,0,0,.3)">'+esc(r.name||'')+'</div>',iconAnchor:[0,12]});
  var m=L.marker(markerLL,{icon:ic}).addTo(map);
  m.bindPopup('<a href="/reach.php?id='+parseInt(r.id,10)+'">'+esc(r.name||'')+'</a>');
  bounds.push(markerLL);
  if(r.track&&r.track.length>1){
    L.polyline(r.track,{color:c,weight:4,opacity:0.7}).addTo(map);
    r.track.forEach(function(p){bounds.push(p)});
  }else if(r.lat_start&&r.lon_start&&r.lat_end&&r.lon_end){
    L.polyline([[r.lat_start,r.lon_start],[r.lat_end,r.lon_end]],{color:c,weight:4,opacity:0.7}).addTo(map);
  }
});
gauges.forEach(function(g){
  var ic=L.divIcon({className:'',html:'<div style="background:#333;color:#ff0;width:14px;height:14px;text-align:center;line-height:14px;font-size:12px;cursor:pointer;border:2px solid #ff0">&#9670;</div>',iconAnchor:[9,9]});
  var m=L.marker([g.lat,g.lon],{icon:ic,zIndexOffset:1000}).addTo(map);
  m.bindPopup(esc(g.name||''));
  bounds.push([g.lat,g.lon]);
});
if(bounds.length>1){map.fitBounds(bounds,{padding:[40,40]})}
else if(bounds.length===1){map.setView(bounds[0],12)}
})();
