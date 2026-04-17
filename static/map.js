(function(){
var map=L.map('map');
var topo=L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{
  maxZoom:17,attribution:'OpenTopoMap'});
var street=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
  maxZoom:19,attribution:'OpenStreetMap'});
var sat=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
  maxZoom:18,attribution:'Esri'});
street.addTo(map);
L.control.layers({'Topo':topo,'Street':street,'Satellite':sat}).addTo(map);

var colors={okay:'#4caf50',low:'#e8a735',high:'#e53935',unknown:'#2196F3'};

function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}

var mtime=document.getElementById('map').dataset.mtime||'';
fetch('/static/reaches.geojson?v='+mtime).then(function(r){return r.json()}).then(function(data){
  var geojsonLayer=L.geoJSON(data,{
    style:function(f){
      return {color:colors[f.properties.status]||colors.unknown,weight:3,opacity:0.7};
    },
    pointToLayer:function(f,ll){
      return L.circleMarker(ll,{radius:6,fillColor:colors[f.properties.status]||colors.unknown,
        color:'#333',weight:1,fillOpacity:0.8});
    },
    onEachFeature:function(f,layer){
      var p=f.properties;
      var badge='<span style="color:'+(colors[p.status]||colors.unknown)+'">&#9679;</span> '+esc(p.status||'');
      layer.bindPopup('<b><a href="/description.php?id='+parseInt(p.id,10)+'">'+esc(p.name||'')+'</a></b><br>'+badge);
    }
  }).addTo(map);
  if(data.features.length)map.fitBounds(geojsonLayer.getBounds().pad(0.05));else map.setView([44.0,-120.5],7);
}).catch(function(){map.setView([44.0,-120.5],7)});

var legend=L.control({position:'bottomright'});
legend.onAdd=function(){
  var d=L.DomUtil.create('div','legend');
  d.innerHTML='<b>Status</b><br>'+
    '<i style="background:#4caf50"></i>Okay<br>'+
    '<i style="background:#e8a735"></i>Low<br>'+
    '<i style="background:#e53935"></i>High<br>'+
    '<i style="background:#2196F3"></i>Unknown';
  return d;
};
legend.addTo(map);
})();
