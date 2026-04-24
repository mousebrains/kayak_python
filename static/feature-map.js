// Leaflet initialiser for gauge + reach detail pages.
//
// Reads an element with id="feature-map" (or legacy id="reach-map") carrying:
//   data-points      JSON object {Label: "lat,lon", ...} — labelled markers.
//   data-track       JSON array [[lat,lon], ...] — river polyline, or null.
//   data-track-color CSS colour for the polyline (default #2196F3).
//
// Single-marker (gauge) pages omit data-track; reach pages include it.
(function(){
var el=document.getElementById('feature-map')||document.getElementById('reach-map');
if(!el)return;
var pts=JSON.parse(el.dataset.points);
var track=el.dataset.track?JSON.parse(el.dataset.track):null;
var trackColor=el.dataset.trackColor||'#2196F3';
var map=L.map(el);
var street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
  attribution:'OpenStreetMap',maxZoom:19});
var topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{
  attribution:'OpenTopoMap',maxZoom:17});
var satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
  attribution:'Esri',maxZoom:19});
topo.addTo(map);
L.control.layers({'Topo':topo,'Street':street,'Satellite':satellite}).addTo(map);
var bounds=[];
var colors={'Put-in':'#1a7a1a','Gauge':'#1b5591','Take-out':'#b30000'};
for(var k in pts){
  var c=pts[k].split(',');
  var ll=[parseFloat(c[0]),parseFloat(c[1])];
  var color=colors[k]||'#1b5591';
  var ic=L.divIcon({className:'',html:'<div style="background:'+color+';color:#fff;padding:2px 6px;border-radius:3px;font:bold 12px sans-serif;white-space:nowrap;cursor:pointer">'+k+'</div>',iconAnchor:[0,12]});
  var m=L.marker(ll,{icon:ic}).addTo(map);
  bounds.push(ll);
  (function(lat,lon){m.on('click',function(){window.open('https://www.google.com/maps?q='+lat+','+lon,'_blank')})})(ll[0],ll[1]);
}
if(track){
  L.polyline(track,{color:trackColor,weight:6,opacity:0.6}).addTo(map);
  track.forEach(function(p){bounds.push(p)});
  // Connect put-in/take-out markers to trace endpoints with dashed lines
  var dash={color:'#666',weight:2,opacity:0.6,dashArray:'6,6'};
  if(pts['Put-in']){
    var pi=pts['Put-in'].split(',');
    var piLL=[parseFloat(pi[0]),parseFloat(pi[1])];
    L.polyline([piLL,track[0]],dash).addTo(map);
  }
  if(pts['Take-out']){
    var to=pts['Take-out'].split(',');
    var toLL=[parseFloat(to[0]),parseFloat(to[1])];
    L.polyline([track[track.length-1],toLL],dash).addTo(map);
  }
}
if(bounds.length>1){map.fitBounds(bounds,{padding:[40,40]})}
else if(bounds.length===1){map.setView(bounds[0],13)}
})();
