<?php
function include_footer(): void {
    echo <<<HTML
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js')</script>
</body>
</html>
HTML;
}
