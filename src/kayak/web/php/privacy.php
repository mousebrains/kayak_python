<?php
declare(strict_types=1);
/**
 * Privacy Policy page.
 */
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/prose.php';

header('Cache-Control: public, max-age=300');
include_header(
    'Privacy Policy — ' . Config::site('site_name', 'River Levels'),
    '',
    'Privacy policy for this river levels site — what data we collect and how we use it.'
);
?>
<!-- Annual review trigger: next review 2027-05-12 -->
<div class="prose">
<?php $__prose = prose_fragment('privacy'); if ($__prose !== null) { echo $__prose; } else { ?>
<h2>Privacy Policy</h2>
<p class="updated">Generic fallback. The deploying dataset should provide its
own privacy policy.</p>

<h3>Data We Collect</h3>
<p>This river levels engine is designed to collect minimal data:</p>
<ul>
  <li><strong>Server access logs:</strong> When you visit a page, our web server records
      your IP address, the page requested, your browser's user-agent string, the referring
      URL, and a timestamp. This is standard web server logging.</li>
  <li><strong>Cookies, if contribution features are enabled:</strong> Browsing alone sets no
      application cookies. If you sign in to propose edits or leave a comment, the site
      uses session and form-security cookies for authentication and CSRF protection.</li>
  <li><strong>Contributor email address:</strong> If you sign in, we store your email
      address so we can send you the one-time login link, notify you when the maintainer
      reviews your proposed edits, and attribute your approved contributions to you
      on the site if you provide a display name.</li>
  <li><strong>Proposed edits and comments:</strong> Anything you submit through the
      Comment form is stored in our database for the maintainer to review.</li>
  <li><strong>No analytics or tracking:</strong> We do not use Google Analytics, Facebook
      pixels, or any third-party tracking services.</li>
</ul>

<h3>Login Email and Bot Protection</h3>
<p>When editor or contact features are enabled, the deployment may use outgoing
email and bot-protection services configured by the operator. Those services may
process the information needed to deliver email or verify that a form submission
is not automated abuse.</p>

<h3>How We Use Server Logs</h3>
<p>Access logs are used solely for:</p>
<ul>
  <li>Diagnosing technical problems and errors</li>
  <li>Identifying and blocking malicious traffic (automated scanners, exploit attempts)</li>
  <li>Understanding which river gauges are most frequently viewed, to prioritize data coverage</li>
</ul>
<p>Logs are retained on the server and are not shared with or sold to any third party.</p>

<h3>Third-Party Services</h3>
<p>The interactive map page loads resources from third-party tile providers to display
base maps:</p>
<ul>
  <li><a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> (street map tiles)</li>
  <li><a href="https://opentopomap.org/about">OpenTopoMap</a> (topographic map tiles)</li>
  <li><a href="https://www.esri.com/en-us/privacy/overview">Esri</a> (satellite imagery tiles)</li>
  <li><a href="https://leafletjs.com">Leaflet</a> (mapping library, served locally)</li>
</ul>
<p>These services may log your IP address when your browser requests map tiles.
Their respective privacy policies apply to that data.</p>

<h3>Data Sources</h3>
<p>River level and gauge data displayed on this site is aggregated from public
government sources including USGS, NOAA/NWS, USACE, USBR, and state water resource
agencies. This is publicly available data; no personal information is involved.</p>

<h3>Children's Privacy</h3>
<p>This site does not knowingly collect any personal information from anyone,
including children under 13.</p>

<h3>Your Rights</h3>
<p>You can ask us to:</p>
<ul>
  <li><strong>Delete your account and associated data.</strong> Email
      the site operator with the email address you registered. The operator can
      delete your editor record, login sessions, pending magic-link tokens, and
      proposed edits/comments. The historical audit trail may be retained for
      site-integrity purposes with the identity link severed.</li>
  <li><strong>Export your contributions.</strong> Ask the operator for a copy of
      your editor record, proposed edits, and the portion of the audit trail
      attributed to you.</li>
  <li><strong>Update your display name</strong> from your account page after signing in.</li>
</ul>
<p>Cookies and short-lived data we retain on a schedule:</p>
<ul>
  <li>Login session cookie (<code>ed_sess</code>): 7 days. The corresponding server-side
      session record is deleted approximately 90 days after expiry.</li>
  <li>One-time login link tokens (<code>editor_magic_link</code>): 30-minute validity;
      records are deleted approximately 90 days after expiry.</li>
  <li>Audit trail of edits made to site content: retained indefinitely; identity link
      is broken when you request account deletion (see above).</li>
</ul>
<p>If you have other questions or concerns about your data, please contact the
site operator.</p>

<h3>Changes to This Policy</h3>
<p>If this policy changes, the updated version will be posted on this page with a
revised date.</p>
<?php } ?>
</div>
<?php include_footer(); ?>
