<?php
declare(strict_types=1);

function include_footer(): void {
    echo <<<HTML
</main>
<footer>
Data sourced from USGS, NOAA, USACE, USBR, and other government agencies.
</footer>
<script src="/static/levels.js" defer></script>
</body>
</html>
HTML;
}
