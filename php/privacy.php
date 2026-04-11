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
<h1><?= $title ?></h1>
<p class="updated">Last updated: March 23, 2026</p>

<p>This website (<strong>levels.wkcc.org</strong>) is operated by the
<a href="https://wkcc.org">Willamette Kayak and Canoe Club</a> (WKCC) to provide
real-time river level, flow, and gauge data for paddlers in the Pacific Northwest.</p>

<h2>Data We Collect</h2>
<p>This site collects <strong>minimal data</strong>:</p>
<ul>
  <li><strong>Server access logs:</strong> When you visit a page, our web server records
      your IP address, the page requested, your browser's user-agent string, the referring
      URL, and a timestamp. This is standard web server logging.</li>
  <li><strong>No cookies for visitors:</strong> This site does not set or read any cookies during
      normal browsing. The administrative editing interface uses a server-side session cookie
      scoped to that single page; it is not used for tracking.</li>
  <li><strong>No analytics or tracking:</strong> We do not use Google Analytics, Facebook
      pixels, or any third-party tracking services.</li>
  <li><strong>No personal accounts:</strong> There is no user registration or login system.</li>
  <li><strong>No data collection forms:</strong> The site does not collect names, email
      addresses, or other personal information through forms.</li>
</ul>

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
  <li><a href="https://www.unpkg.com">unpkg.com</a> (Leaflet mapping library)</li>
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
</body>
</html>
