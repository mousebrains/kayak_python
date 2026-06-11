<?php
declare(strict_types=1);
/**
 * Disclaimer / use-at-your-own-risk page.
 */
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/prose.php';

header('Cache-Control: public, max-age=300');
include_header(
    'Disclaimer — ' . Config::site('site_name', 'River Levels'),
    '',
    'Use-at-your-own-risk notice for this river levels site. Paddling is inherently dangerous; you are responsible for your own safety.'
);
?>
<div class="prose">
<?php $__prose = prose_fragment('disclaimer'); if ($__prose !== null) { echo $__prose; } else { ?>
<h2>Disclaimer</h2>
<p class="updated">Generic fallback. The deploying dataset should provide its
own disclaimer.</p>

<div class="warn">
<p><strong>Whitewater paddling, kayaking, canoeing, rafting, and any other
activity on moving water are inherently dangerous and can result in serious
injury or death.</strong> Your safety on the water is your responsibility —
not the responsibility of this website, its operators, contributors, or the
organization that runs this deployment.</p>
</div>

<h3>Information Is Provided "As Is"</h3>
<p>This site aggregates river level, flow, gauge, and temperature data from
public government sources (USGS, NOAA, USACE, USBR, state agencies, and
others) and publishes reach descriptions contributed by volunteer paddlers.
All information is provided <strong>"as is" and without warranty of any
kind</strong>, either express or implied, including but not limited to
warranties of accuracy, completeness, timeliness, fitness for a particular
purpose, or non-infringement.</p>

<p>Gauge readings may be <strong>delayed, incorrect, missing, or
misinterpreted</strong>. Sensors fail. Upstream stations may not reflect
conditions at your put-in. Guidebook entries, class ratings, recommended
flow ranges, hazard notes, and map geometry may be <strong>inaccurate,
outdated, or wrong</strong>. Rivers change — log jams form, rapids wash out,
dams spill, weather shifts, landowners revoke access — and the site may not
reflect current reality.</p>

<h3>You Are Responsible for Your Own Safety</h3>
<p>Before you get on the water, it is <strong>your responsibility</strong> to:</p>
<ul>
  <li>Verify river levels, weather, and hazards through multiple independent
      sources, not just this site.</li>
  <li>Scout unfamiliar rapids and know your own and your group's skill
      limits.</li>
  <li>Carry and know how to use appropriate safety equipment (PFD, helmet,
      throw bag, first-aid kit, communication).</li>
  <li>Obtain any required permits and respect public and private land access
      rules.</li>
  <li>Make the final go / no-go decision yourself — based on what you see
      on the river that day, not what a website said the day before.</li>
</ul>

<p>If you are unsure, <strong>stay off the water</strong>. No gauge reading,
guidebook description, or trip report on this site overrides your
on-the-water judgment.</p>

<h3>No Liability</h3>
<p>To the fullest extent permitted by law, the operators, maintainers, and
contributors of this website <strong>disclaim all liability</strong> for any
loss, injury, illness, death, property damage, or other harm resulting from use
of, reliance on, or inability to use this site or any information it contains.
Your use of this site is entirely at your own risk.</p>

<p>This disclaimer applies whether the claim arises in contract, tort
(including negligence), strict liability, or any other legal theory, and
whether or not the operators have been advised of the possibility of such
damages.</p>

<h3>Third-Party Data and Links</h3>
<p>We do not control the government data feeds this site aggregates, nor
any third-party sites linked from our pages. Their accuracy, availability,
and policies are their own.</p>

<h3>Changes to This Disclaimer</h3>
<p>This disclaimer may be updated from time to time. The revised version
will be posted on this page with a new date. Your continued use of the site
constitutes acceptance of the current version.</p>
<?php } ?>
</div>
<?php include_footer(); ?>
