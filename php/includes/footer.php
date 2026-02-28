<?php
function include_footer(): void {
    echo <<<HTML
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
<script>
document.querySelectorAll('time[datetime]').forEach(function(el){
  var d=new Date(el.getAttribute('datetime'));
  if(isNaN(d))return;
  var mm=d.getMonth()+1,dd=d.getDate();
  var hh=d.getHours(),mi=d.getMinutes();
  el.textContent=(mm<10?'0':'')+mm+'/'+(dd<10?'0':'')+dd+' '+(hh<10?'0':'')+hh+':'+(mi<10?'0':'')+mi;
});
</script>
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js')</script>
</body>
</html>
HTML;
}
