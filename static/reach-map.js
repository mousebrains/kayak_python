(function(){
'use strict';
const el=document.getElementById('reach-map');
if(!el)return;
const pts=JSON.parse(el.dataset.points);
const track=el.dataset.track?JSON.parse(el.dataset.track):null;
const trackColor=el.dataset.trackColor||'#2196F3';
const map=L.map('reach-map');
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
const gaugeId=parseInt(el.dataset.gaugeId||'0',10);
for(const k in pts){
  const c=pts[k].split(',');
  const ll=[parseFloat(c[0]),parseFloat(c[1])];
  const color=colors[k]||'#1b5591';
  const dot=L.circleMarker(ll,{radius:6,fillColor:color,color:'#222',weight:1,fillOpacity:0.95}).addTo(map);
  dot.bindTooltip(k,{permanent:true,direction:'right',offset:[6,0],className:'map-label',interactive:true});
  bounds.push(ll);
  (function(lat,lon,label){
    const onClick=function(){
      if(label==='Gauge' && gaugeId){
        window.location.href='/gauge.php?id='+gaugeId;
      }else{
        window.open('https://www.google.com/maps?q='+lat+','+lon,'_blank');
      }
    };
    dot.on('click',onClick);
    const tt=dot.getTooltip();
    if(tt)tt.on('click',onClick);
  })(ll[0],ll[1],k);
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
if(bounds.length>1){map.fitBounds(bounds,{padding:[40,40]})}
else if(bounds.length===1){map.setView(bounds[0],13)}
})();
