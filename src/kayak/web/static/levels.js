'use strict';

// Track header height so sticky thead can sit flush below it
(function(){
  var hdr=document.querySelector('header');
  if(!hdr||typeof ResizeObserver==='undefined')return;
  function setH(){document.documentElement.style.setProperty('--header-h',hdr.offsetHeight+'px');}
  setH();
  new ResizeObserver(setH).observe(hdr);
})();

// Convert UTC timestamps to local time
document.querySelectorAll('time[datetime]').forEach(function(el){
  var d=new Date(el.getAttribute('datetime'));
  if(isNaN(d))return;
  var mm=d.getMonth()+1,dd=d.getDate();
  var hh=d.getHours(),mi=d.getMinutes();
  el.textContent=(mm<10?'0':'')+mm+'/'+(dd<10?'0':'')+dd+' '+(hh<10?'0':'')+hh+':'+(mi<10?'0':'')+mi;
});

// Clickable table rows — mouse and keyboard
(function(){
  var tbl=document.querySelector('.levels');
  if(!tbl)return;
  tbl.querySelectorAll('tr[data-href]').forEach(function(r){
    r.setAttribute('role','link');
    r.setAttribute('tabindex','0');
  });
  function nav(e){
    if(e.target.closest('a'))return;
    var r=e.target.closest('tr[data-href]');
    if(r)location.href=r.dataset.href;
  }
  tbl.addEventListener('click',nav);
  tbl.addEventListener('keydown',function(e){
    if(e.key==='Enter'||e.key===' '){e.preventDefault();nav(e);}
  });
})();

// Lazy-load sparkline SVGs after first paint (only on pages with placeholders)
if(document.querySelector('span.spark[data-gid]')){
  fetch('/static/sparklines.json').then(function(r){return r.json()}).then(function(data){
    document.querySelectorAll('span.spark[data-gid]').forEach(function(el){
      var svg=data[el.dataset.gid];
      if(svg)el.innerHTML=svg;
    });
  }).catch(function(){});
}

// Service worker — network-first with cache fallback for slow connections
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js');
}
