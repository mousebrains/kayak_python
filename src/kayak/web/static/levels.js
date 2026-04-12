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

// Service worker — unregister any stale root-scope SW, then register with default scope
if('serviceWorker' in navigator){
  navigator.serviceWorker.getRegistrations().then(function(regs){
    regs.forEach(function(r){if(r.scope!==location.origin+'/static/')r.unregister();});
  }).then(function(){
    return navigator.serviceWorker.register('/static/sw.js');
  }).catch(function(){});
}
