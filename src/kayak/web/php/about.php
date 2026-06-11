<?php
declare(strict_types=1);
/**
 * About page — history and design philosophy of the site.
 */
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/prose.php';

header('Cache-Control: public, max-age=300');
include_header(
    'About — ' . Config::site('site_name', 'River Levels'),
    '',
    'How this river levels site came to be — a project to make real-time river data available to paddlers.'
);
?>
<div class="prose">
<?php $__prose = prose_fragment('about'); if ($__prose !== null) { echo $__prose; } else { ?>
<h2>About</h2>
<p>This site publishes river level, flow, and gauge information for paddlers.
It is built from a dataset maintained by the organization that runs this
deployment.</p>

<h3>How It Works</h3>
<p>The engine aggregates public water-data feeds, combines them with reviewed
reach metadata, and builds lightweight pages for quick use in the field.</p>

<p>Each deployment supplies its own dataset, site identity, regional links, map
layers, and long-form page text. If this generic fallback appears on a public
site, the deployment has not yet provided dataset-owned about-page prose.</p>
<?php } ?>
</div>
<?php include_footer(); ?>
