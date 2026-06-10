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
    'About — ' . Config::site('site_name', 'WKCC River Levels'),
    '',
    'How the WKCC River Levels site came to be — a three-decade project to make real-time river data available to paddlers.'
);
?>
<div class="prose">
<?php $__prose = prose_fragment('about'); if ($__prose !== null) { echo $__prose; } else { ?>
<h2>About</h2>
<p class="updated">Last updated: April 23, 2026</p>

<h3>Origins</h3>
<p>It started with a single paddling trip.</p>

<p>In the 1994&ndash;95 season, Pat Welch ran the North Fork Middle Fork
of the Willamette (NFMF) with Jim Reed. Somewhere between the take-out
and the drive home, the conversation turned to Salmon Creek &mdash; a
nearby run with no gauge of its own. Jim said it would be nice to know
the level before making the drive out there. He had worked out a rough
rule of thumb that related Salmon Creek's level to the NFMF gauge, which
did report: if the NFMF read a certain number, Salmon Creek was probably
in.</p>

<p>That conversation was the seed.</p>

<h3>From a CSV to a website</h3>
<p>Jim was an editor of the third edition of the Willamette Kayak and
Canoe Club's <em>Soggy Sneakers</em>, the club's guide to whitewater in
Oregon. He had already done the work of cataloguing runs &mdash; put-ins,
take-outs, character, hazards, recommended flow ranges &mdash; and he
handed Pat a CSV file of the runs described in the book.</p>

<p>Pat, meanwhile, had been harvesting USACE gauge data. A script to
fetch those gauges, Jim's table of runs, and a way to glue the two
together &mdash; that was all it took to make something genuinely
useful. If you were thinking about paddling Salmon Creek in the morning,
you could finally check the night before.</p>

<p>The first version was a prototype written in Tcl/Tk, running on
Pat's research computer. Rough edges everywhere, but it solved the
problem.</p>

<h3>Growth and rewrites</h3>
<p>Over the years the scope crept outward. More rivers. More states.
More agencies &mdash; NOAA, USGS, USBR, and state water resource
departments &mdash; joined the USACE feeds the site started with. At
its peak the database tracked roughly <strong>three thousand gauges</strong>
across the western United States.</p>

<p>Around 2000, the Tcl/Tk code was replaced with C++. That version
carried the site for years: slow feature additions, quiet fixes, the
occasional partial rebuild. What followed after that was less a single
rewrite than a long series of starts and stops &mdash; pieces were
ported as they needed changing; other pieces waited.</p>

<p>By 2026, the C++ base has been progressively transformed into the
system running today: a Python backbone on top of a SQLite database, a
modular fetch pipeline that speaks each agency's formats, and a static
HTML build for the levels pages layered over PHP for the interactive
pieces. It looks, finally, like a modern web application.</p>

<h3>Design philosophy: thin pipes</h3>
<p>One goal has never changed, and it has gotten more important with
time, not less: <strong>thin pipes</strong>.</p>

<p>Pages are small. Requests are few. Nothing loads from a third-party
tracker. The levels tables are pre-rendered HTML with inlined CSS and
tiny inline SVG sparklines. The site is tuned to work on a high-bandwidth
desktop, yes &mdash; but really it is tuned to work on a 3G phone with
one bar of signal at the put-in, which is, not coincidentally, exactly
the moment you most need to check it.</p>

<p>Every design decision runs through that filter: <em>does this still
work over a slow connection?</em> If the answer is no, it doesn't ship.</p>

<div class="support">
<h3>Supporting this site</h3>
<p>For decades this site has been supported by the
<a href="https://wkcc.org">Willamette Kayak and Canoe Club</a> (WKCC).
If you find it useful and want to help keep it going, please consider
joining or contributing to the club.</p>
<p>Pat's time on this project is entirely voluntary.</p>
</div>
<?php } ?>
</div>
<?php include_footer(); ?>
