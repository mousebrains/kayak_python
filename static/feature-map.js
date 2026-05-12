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
(function(){
'use strict';
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
const el=document.getElementById('feature-map')||document.getElementById('reach-map');
if(!el)return;
const pts=JSON.parse(el.dataset.points);
const track=el.dataset.track?JSON.parse(el.dataset.track):null;
const trackColor=el.dataset.trackColor||'#2196F3';
const reachTracks=el.dataset.reachTracks?JSON.parse(el.dataset.reachTracks):[];
const map=L.map(el);
const street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
  attribution:'OpenStreetMap',maxZoom:19});
const topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{
  attribution:'OpenTopoMap',maxZoom:17});
const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
  attribution:'Esri',maxZoom:19});
topo.addTo(map);
L.control.layers({'Topo':topo,'Street':street,'Satellite':satellite}).addTo(map);
const bounds=[];
const colors={'Put-in':'#1a7a1a','Gauge':'#1b5591','Take-out':'#b30000'};
for(const k in pts){
  const c=pts[k].split(',');
  const ll=[parseFloat(c[0]),parseFloat(c[1])];
  const color=colors[k]||'#1b5591';
  const ic=L.divIcon({className:'',html:'<div style="background:'+color+';color:#fff;padding:2px 6px;border-radius:3px;font:bold 12px sans-serif;white-space:nowrap;cursor:pointer">'+k+'</div>',iconAnchor:[0,12]});
  const m=L.marker(ll,{icon:ic}).addTo(map);
  bounds.push(ll);
  (function(lat,lon){m.on('click',function(){window.open('https://www.google.com/maps?q='+lat+','+lon,'_blank')})})(ll[0],ll[1]);
}
if(track){
  L.polyline(track,{color:trackColor,weight:6,opacity:0.6}).addTo(map);
  track.forEach(function(p){bounds.push(p)});
  // Connect put-in/take-out markers to trace endpoints with dashed lines
  const dash={color:'#666',weight:2,opacity:0.6,dashArray:'6,6'};
  if(pts['Put-in']){
    const pi=pts['Put-in'].split(',');
    const piLL=[parseFloat(pi[0]),parseFloat(pi[1])];
    L.polyline([piLL,track[0]],dash).addTo(map);
  }
  if(pts['Take-out']){
    const to=pts['Take-out'].split(',');
    const toLL=[parseFloat(to[0]),parseFloat(to[1])];
    L.polyline([track[track.length-1],toLL],dash).addTo(map);
  }
}
// Per-reach clickable polylines for the gauge page. Coloured by status
// (low/okay/high), with a dark casing for contrast and a fat invisible
// hit polyline so finger taps register reliably on touch devices.
// Mirrors the styling and hover behaviour of the main /map.html.
if(reachTracks.length){
  const COLORS={low:'#ff6d00',okay:'#76ff03',high:'#ff1744',unknown:'#00b0ff'};
  const REST_LINE={weight:4,opacity:1.0};
  const HOVER_LINE={weight:7,opacity:1.0};
  const REST_CASING={color:'#1a1a1a',weight:5,opacity:0.5,lineJoin:'round',lineCap:'round',interactive:false};
  const HOVER_CASING={weight:9};
  // Casings render below the colored line so bringToFront on hover doesn't
  // disrupt mouseout (same pane trick used on /map.html).
  map.createPane('reach-casings');
  map.getPane('reach-casings').style.zIndex='400';
  map.getPane('reach-casings').style.pointerEvents='none';
  map.createPane('reaches');
  map.getPane('reaches').style.zIndex='410';
  map.createPane('reach-hits');
  map.getPane('reach-hits').style.zIndex='420';

  reachTracks.forEach(function(rt){
    const color=COLORS[rt.status]||COLORS.unknown;
    const visible=L.polyline(rt.points,{
      color:color,weight:REST_LINE.weight,opacity:REST_LINE.opacity,
      lineJoin:'round',lineCap:'round',pane:'reaches'
    }).addTo(map);
    const casing=L.polyline(rt.points,Object.assign({},REST_CASING,{pane:'reach-casings'})).addTo(map);
    const hit=L.polyline(rt.points,{
      weight:18,opacity:0,interactive:true,pane:'reach-hits',
      lineCap:'round',lineJoin:'round'
    }).addTo(map);

    const html='<a class="reach-popup" href="/description.php?id='+parseInt(rt.id,10)+'">'+
      '<div class="rp-name">'+esc(rt.name)+'</div>'+
      (rt.location?'<div class="rp-loc">'+esc(rt.location)+'</div>':'')+
      (rt.classes?'<div class="rp-cls">Class '+esc(rt.classes)+'</div>':'')+
      '</a>';
    hit.bindPopup(html);

    hit.on('mouseover',function(){
      visible.setStyle(HOVER_LINE);
      casing.setStyle(HOVER_CASING);
      visible.bringToFront();
    });
    hit.on('mouseout',function(){
      visible.setStyle(REST_LINE);
      casing.setStyle({weight:REST_CASING.weight});
    });
    rt.points.forEach(function(p){bounds.push(p)});
  });
}

if(bounds.length>1){map.fitBounds(bounds,{padding:[40,40]})}
else if(bounds.length===1){map.setView(bounds[0],13)}
})();
