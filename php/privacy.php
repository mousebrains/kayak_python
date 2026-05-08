<?php
declare(strict_types=1);
/**
 * Privacy Policy page.
 */
$title = "Privacy Policy";
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title><?= $title ?> — WKCC River Levels</title>
<meta name="description" content="Privacy policy for the WKCC River Levels site — what data we collect and how we use it.">
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       max-width: 48em; margin: 2em auto; padding: 0 1em; line-height: 1.6; color: #222; }
h1 { font-size: 1.5em; border-bottom: 1px solid #ccc; padding-bottom: .3em; }
h2 { font-size: 1.15em; margin-top: 1.5em; }
a { color: #2060A0; }
.updated { color: #666; font-size: .9em; }
</style>
</head>
<body>
<main>
<h1><?= $title ?></h1>
<p class="updated">Last updated: May 1, 2026</p>

<p>This website (<strong>levels.wkcc.org</strong>) is operated by the
<a href="https://wkcc.org">Willamette Kayak and Canoe Club</a> (WKCC) to provide
real-time river level, flow, and gauge data for paddlers in the Pacific Northwest.</p>

<h2>Data We Collect</h2>
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

<h2>Login Email and Bot Protection</h2>
<p>Login links are emailed via our server's outgoing mail, which relays through
Google's mail infrastructure. See
<a href="https://policies.google.com/privacy">Google's privacy policy</a>
for details on that step. Sign-in and contact forms use
<a href="https://www.cloudflare.com/privacypolicy/">Cloudflare Turnstile</a>
to deter automated abuse. Turnstile is typically invisible (no puzzle
challenge); when active it sees your IP address and browser details.</p>

<h2>How We Use Server Logs</h2>
<p>Access logs are used solely for:</p>
<ul>
  <li>Diagnosing technical problems and errors</li>
  <li>Identifying and blocking malicious traffic (automated scanners, exploit attempts)</li>
  <li>Understanding which river gauges are most frequently viewed, to prioritize data coverage</li>
</ul>
<p>Logs are retained on the server and are not shared with or sold to any third party.</p>

<h2>Third-Party Services</h2>
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

<h2>Data Sources</h2>
<p>River level and gauge data displayed on this site is aggregated from public
government sources including USGS, NOAA/NWS, USACE, USBR, and state water resource
agencies. This is publicly available data; no personal information is involved.</p>

<h2>Children's Privacy</h2>
<p>This site does not knowingly collect any personal information from anyone,
including children under 13.</p>

<h2>Your Rights</h2>
<p>Because we collect only server access logs and no personal data, there is generally
no personal data to access, correct, or delete. If you have questions or concerns about
your data, please contact the
<a href="https://wkcc.org">Willamette Kayak and Canoe Club</a>.</p>

<h2>Changes to This Policy</h2>
<p>If this policy changes, the updated version will be posted on this page with a
revised date.</p>

<p style="margin-top: 2em;"><a href="/">← Back to river levels</a></p>
</main>
</body>
</html>
