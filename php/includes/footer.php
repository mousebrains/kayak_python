<?php
function include_footer(): void {
    echo <<<HTML
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
<script src="/static/levels.js"></script>
</body>
</html>
HTML;
}
