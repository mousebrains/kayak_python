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
    'Privacy Policy — ' . Config::site('site_name', 'WKCC River Levels'),
    '',
    'Privacy policy for the WKCC River Levels site — what data we collect and how we use it.'
);
?>
<!-- Annual review trigger: next review 2027-05-12 -->
<div class="prose">
<?php $__prose = prose_fragment('privacy'); if ($__prose !== null) { echo $__prose; } else { ?>
<h2>Privacy Policy</h2>
<p class="updated">Last updated: May 12, 2026</p>

<p>This website (<strong>levels.wkcc.org</strong>) is operated by the
<a href="https://wkcc.org">Willamette Kayak and Canoe Club</a> (WKCC) to provide
real-time river level, flow, and gauge data for paddlers in the Pacific Northwest.</p>

<h3>Data We Collect</h3>
<p>This site collects <strong>minimal data</strong>:</p>
<ul>
  <li><strong>Server access logs:</strong> When you visit a page, our web server records
      your IP address, the page requested, your browser's user-agent string, the referring
      URL, and a timestamp. This is standard web server logging.</li>
  <li><strong>Cookies, if you choose to contribute:</strong> Browsing alone sets no cookies.
      If you sign in to propose edits or leave a comment, we set two cookies:
      <code>ed_sess</code> (a random value identifying your login session, valid for
      seven days) and <code>ed_csrf</code> (a form-submission security token). Both are
      HttpOnly, SameSite=Strict, and marked Secure on HTTPS. They are used only for
      authentication and form security — not for tracking.</li>
  <li><strong>Contributor email address:</strong> If you sign in, we store your email
      address so we can send you the one-time login link, notify you when the maintainer
      reviews your proposed edits, and attribute your approved contributions to you
      on the site (if you provide a display name). You can request deletion at any
      time by contacting the club.</li>
  <li><strong>Proposed edits and comments:</strong> Anything you submit through the
      Comment form is stored in our database for the maintainer to review.</li>
  <li><strong>No analytics or tracking:</strong> We do not use Google Analytics, Facebook
      pixels, or any third-party tracking services.</li>
</ul>

<h3>Login Email and Bot Protection</h3>
<p>Login links are emailed via our server's outgoing mail, which relays through
Google's mail infrastructure. See
<a href="https://policies.google.com/privacy">Google's privacy policy</a>
for details on that step. Sign-in and contact forms use
<a href="https://www.cloudflare.com/privacypolicy/">Cloudflare Turnstile</a>
to deter automated abuse. Turnstile is typically invisible (no puzzle
challenge); when active it sees your IP address and browser details.</p>

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
      <a href="https://wkcc.org">the club</a> with the email address you registered.
      We will delete your editor record, any login sessions, pending magic-link tokens,
      and proposed edits/comments. The historical audit trail (which fields were changed
      on which reach, and when) is retained for site-integrity purposes, but the link
      back to your identity is severed.</li>
  <li><strong>Export your contributions.</strong> Email the club with the same address;
      we will send you a JSON copy of your editor record, your proposed edits, and the
      portion of the audit trail attributed to you.</li>
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
<a href="https://wkcc.org">Willamette Kayak and Canoe Club</a>.</p>

<h3>Changes to This Policy</h3>
<p>If this policy changes, the updated version will be posted on this page with a
revised date.</p>
<?php } ?>
</div>
<?php include_footer(); ?>
