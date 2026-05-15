# Plan — Card-ify Associated Reaches on phone portrait

> **Cross-check:** plan drafted 2026-05-15 against `main` at `af64c10`.
>
> **Iter log:**
> - iter 1 (2026-05-15): 5 findings — see below.
> - iter 2 (2026-05-15): 5 findings — (A) **status helper citation
>   was wrong.** Plan said `_compute_reach_status_for_gauge_page()
>   at :251`; the real function is `_compute_reach_statuses()` at
>   `gauge_detail.php:258`, calling `_classify_reach_status()` at
>   `:220-248`. Docblock `:218` documents return type as
>   `'low'|'okay'|'high'|'unknown'` — exactly the four values the
>   `tr[data-status="..."]` CSS selectors target. (B) **CSS vars
>   `--c-low / --c-okay / --c-high / --c-text-muted` confirmed
>   defined at `style.css:5,17-19`, with dark-mode overrides at
>   `:419-421`. No extra CSS plumbing needed — the status dot
>   inherits both color schemes for free. (C) **CSS cascade safe:**
>   no specificity collision between new `.readings-table.assoc-reaches
>   tr` rules and existing `table.levels tr` rules — different
>   selector roots, and neither table appears on the same page as
>   the other. (D) **`data-status` emission is an established
>   pattern**: `src/kayak/web/build/levels.py:217` emits
>   `data-status="<status>"` directly on `<tr>`;
>   `src/kayak/web/build/gauges.py:406` does the same. The new PHP
>   edit will mirror this convention exactly. (E) **Accessibility
>   precedent:** `display:grid` on `<tr>` (in `table.levels` card
>   mode at `:158`) breaks ARIA table semantics for screen readers.
>   This is accepted in the existing pattern. Worth a short comment
>   in the new rule block so future maintainers know it's
>   deliberate, not an oversight.
> - iter 3 (2026-05-15): 5 findings — (A) **Header row hidden via
>   `<thead>` wrapper.** Current `_render_associated_reaches()`
>   emits the header `<tr>` directly inside `<table>` with no
>   `<thead>` (`gauge_detail.php:571`). The existing
>   `table.levels` card mode hides headers via
>   `table.levels thead{display:none}` (`style.css:156`). To reuse
>   that idiom cleanly, wrap the header in `<thead>` and the data
>   rows in `<tbody>` in the PHP emission. Without wrappers we'd
>   need `tr:has(th)` or `tr:first-child` selectors, which work
>   but read less clearly. (B) **`.readings-table td` border must
>   reset in card mode.** Base rule at `style.css:283` declares
>   `border:1px solid var(--c-border)` on every cell — those
>   borders draw as a grid lattice inside our card layout,
>   breaking the "single card" look. Mirror `table.levels td{
>   padding:0; border:none }` at `:171` inside the new card CSS
>   block. (C) **Name link stays `display:inline` in card mode.**
>   Existing `table.levels td.td-name a` switches from `block`
>   (desktop, `:122`) to `inline` (phone card, `:189`). Inline keeps
>   the tap target on the name text only — not the whole card. The
>   plan should not invent a "click anywhere on the card"
>   interaction (would need JS or position:absolute tricks that
>   the rest of the site doesn't use). Tap on name = navigate;
>   parity with existing pattern. (D) **Defensive
>   `htmlspecialchars($status)` on `data-status` attribute.** The
>   status value is documented as a strict union, but
>   `levels.py:217` and `gauges.py:406` both escape it before
>   emission (`html_mod.escape(status)`). Mirror the discipline
>   for parity even though the union is sealed. (E) **Inline
>   `style="..."` attrs are CSP-allowed.** `style-src 'self'
>   'unsafe-inline'` per `conf/security-headers.conf:27` — the
>   existing inline styles at `gauge_detail.php:561, 580` are
>   accepted. New rules go in `style.css` for cleanliness, but
>   inline fallbacks are legal if needed.
> - iter 4 (2026-05-15): 4 findings — (A) **Explicit grid template.**
>   The phone-card layout is 2 columns × 2 rows:
>   `grid-template-columns: 1fr auto; column-gap:.4rem;
>   padding:.35rem .5rem` on `tr` — mirrors the four-column variant
>   at `style.css:163-167`. Cell placement:
>   `td.td-name {grid-row:1; grid-column:1; min-width:0}`,
>   `td[data-label="Class"] {grid-row:1; grid-column:2;
>   text-align:right}`,
>   `td[data-label="Location"] {grid-row:2; grid-column:1; ...
>   muted, ellipsis}`,
>   `td[data-label="Length"] {grid-row:2; grid-column:2;
>   text-align:right; muted}`. (B) **`min-width:0` on `td.td-name`
>   prevents grid-blowout** on long class strings ("II · III · IV
>   · V") that could otherwise force column 2 to expand past 1fr.
>   Standard CSS Grid fix, same reason `table.levels td.td-name`
>   doesn't need it at `:184` (because it uses `1 / 3` column
>   span — different layout). (C) **Row 2 cells need muted
>   styling.** Mirror `:204-218`: `font-size:.8rem;
>   color:var(--c-text-muted)` on both Location and Length cells
>   so they read as secondary info. The desktop view inherits the
>   base `.readings-table td{font-size:.85rem}` (no muted color)
>   so this only applies in card mode. (D) **Empty Location is
>   self-handling.** With iter 3 (B)'s `td{padding:0; border:none}`
>   reset, an empty Location cell contributes zero height. Row 2
>   collapses naturally when both Location and Length are empty.
>   Test fixture
>   (`tests/php/GaugeIntegrationTest.php:95-107`'s "Clackamas
>   Above Estacada" reach) has `description=NULL` — exercises this
>   path in the integration test without any extra fixture work.
> - iter 5 (2026-05-15, stopping): 2 findings — (A) **Test
>   specificity tradeoff.** The new positive assertion locks in
>   only `class="readings-table assoc-reaches"`. It does NOT lock
>   in `<thead>`, `data-status`, or per-cell `data-label`
>   attributes — those are CSS plumbing, not behavior the test
>   needs to defend. Verifying card layout depends on visual /
>   in-browser checks (`/gauge.php?id=14` on a phone-portrait
>   viewport at `levels-test.wkcc.org`). Acceptable: the
>   integration test catches accidental class-name drops, the
>   developer catches grid-layout regressions in-browser.
>   (B) **Convergence pattern.** Findings per iter: 5 → 5 → 5 → 4
>   → 2. Diminishing returns; remaining tradeoffs (test depth,
>   future column-decisions on other `.readings-table` consumers)
>   are user-facing choices, not implementation gaps. Stopping.
>
> Dates absolute. References `file:line` against current `main`.

## Why

Per user report 2026-05-15, on a phone in portrait
(`https://levels-test.wkcc.org/gauge.php?id=14`), the Associated
Reaches table has the rightmost **Status** column rendered off the
right edge of the screen. The table grew from 5 → 6 columns in
commit `64f9ef0` (Item 4 of `docs/done/PLAN_map_and_ui_tweaks.md`: added
Location, dropped Watershed — net zero columns, but the new
Location cell carries more characters than the old Watershed cell
on most rows, pushing the table wider). On a ~390 px portrait
viewport (`main` has `padding:.5rem`), 6 columns no longer fit.

User chose "card-ify it" over "hide columns" or "horizontal
scroll". This plan implements the card layout that mirrors the
phone-portrait pattern already in place for `table.levels`
(`src/kayak/web/static/style.css:149-229`).

## Scope inventory (verified against current `main`)

**PHP rendering** — `php/includes/gauge_detail.php:558-585`:

- `_render_associated_reaches()` emits a `<table class="readings-table">`
  with header `Name / Location / River / Class / Length / Status`
  (`:571`).
- Per-row `<td>` emission at `:582` is unstructured — no `data-label`
  attrs and no per-cell classes. Both are required for the
  existing card-pattern CSS (which targets `td.td-name`,
  `td[data-label="Location"]`, etc. — see
  `src/kayak/web/static/style.css:184-217`).
- Reach status comes from `$reach_status_by_id` (precomputed by
  `_compute_reach_statuses()` at `:258`, dispatching to
  `_classify_reach_status()` at `:220-248`). Docblock at `:218`
  documents the return type as `'low'|'okay'|'high'|'unknown'`.

**CSS** — `src/kayak/web/static/style.css`:

- `.readings-table` (`:282-285`) — base styles. NO mobile rules
  today.
- `table.levels` card pattern (`:149-229`) — the model to mirror:
  - `tr` becomes `display:grid`; `thead` hidden; cells positioned
    onto a 4-col grid.
  - Status encoded as a dot before the name via
    `tr[data-status="low|okay|high"] td.td-name::before`
    (`:223-228`).
  - Secondary cells hidden via `display:none` (e.g.,
    `td.secondary[data-label="Watershed"]` at `:180`).
- `table.levels` tablet pattern (`:235-246`) — at 640-1023px,
  hide `.secondary` cells and cap `td[data-label="Location"]` to
  `22ch` with ellipsis. Associated Reaches has no `.secondary`
  cells today, but the Location-cap pattern is reusable.

**Other `.readings-table` consumers — must NOT regress**:

- `php/includes/gauge_detail.php:372` — gauge readings table
  (Type / Value / Time / Change-per-hour / Status). 5 cols.
- `php/includes/gauge_detail.php:539` — associated sources table
  (ID / Name / Agency / Observations / Latest). 5 cols.
- `php/includes/description_detail.php:305` — description readings
  table (same 5-col shape as gauge readings).
- `php/data.php:124` — per-source data export table (Time / Src
  + N data-type columns).

These four tables share the `.readings-table` selector with
Associated Reaches. Any CSS that touches `.readings-table`
unconditionally would change all five. The fix is to add a
*second* class (e.g., `assoc-reaches`) to the Associated Reaches
table and scope the new card rules to `.readings-table.assoc-reaches`.

**Tests** — `tests/php/`:

- `GaugeIntegrationTest.php:182-208` asserts the body contains the
  literal strings `Associated Reaches`, `Clackamas Above Estacada`
  (the reach name), and other readings labels. No assertion on the
  table class string.
- `GaugeIntegrationTest.php:210-228` (no-readings case) asserts the
  body does **not** contain `class="readings-table"`. This passes
  because the no-readings gauge also has no associated reaches
  (the existing fixture wires only one reach to the with-data
  gauge — `:93`). Substring check survives adding a second class
  (we'd emit `class="readings-table assoc-reaches"`).
- `DescriptionIntegrationTest.php:179` — same substring assertion
  for the no-readings description page; unaffected.

**Live constraints**:

- PHP-FPM lacks `mbstring` (project memory
  `reference_php_no_mbstring`): use `strlen`/`substr`, not `mb_*`.
  N/A for this change — no string manipulation in the PHP edit;
  only adding cell classes + `data-label` attributes.
- nginx CSP blocks inline scripts/handlers (memory
  `feedback_csp_no_inline`): N/A — change is HTML + external CSS.
- PHPStan level 8 + `composer fix-check` enforced in CI: the PHP
  edit is `echo` only, no new types or method signatures.

## Iter 1 findings (2026-05-15)

(A) **`data-label` attrs are absent from the current PHP emission.**
`gauge_detail.php:582` emits raw `<td>$location</td>` without
`data-label="Location"`. The existing card pattern targets
`td[data-label="..."]`, so plain `<td>` cells won't position
themselves on the grid. Need to add `data-label` + per-cell
class (`td-name`, `td-status`, etc.) to every cell.

(B) **Class-scoping via second class is necessary.** The four
other `.readings-table` consumers (gauge readings, associated
sources, description readings, data.php data export) must not
inherit card behavior. Add `class="readings-table assoc-reaches"`
and scope new rules with the combined selector
`.readings-table.assoc-reaches`.

(C) **Status text is redundant once the dot is shown.** The
existing `table.levels` card uses
`tr[data-status="low|okay|high"] td.td-name::before` to prepend a
colored dot to the name. Doing the same for Associated Reaches
makes the Status column redundant on phone — drop it
(`display:none`) and keep all the status signal in the dot. Saves
horizontal space; matches existing pattern.

(D) **River cell is low-signal on a per-gauge page.** Plan
`PLAN_map_and_ui_tweaks.md` iter-3 (B) noted River is uniformly
"Clackamas" across all rows on `/gauge.php?id=14` — same on most
gauges. Hide it on phone portrait. Tap the Name link to see the
full reach record.

(E) **Existing card uses 4-col grid; we need a 2-col grid.**
`style.css:163` declares `grid-template-columns:auto 1fr auto auto`
for `table.levels` — the four columns are class·secondary / name
/ flow / sparkline. Associated Reaches has no flow/sparkline; the
phone shape is two columns at most: dot+name (left) /
metadata (right). Don't try to inherit the grid; declare our own
on `.readings-table.assoc-reaches tr`.

## Approach

### Card layout (phone portrait — max-width:639px)

```
[●] Bold Name Link ─────────────  Class
Location (muted, ellipsized) ───  Length
```

- 2-col 2-row CSS Grid: `grid-template-columns: 1fr auto`.
- Row 1, col 1: status dot (color from `tr[data-status]`) + bold
  Name link. `min-width:0` prevents grid-blowout on long Class
  strings.
- Row 1, col 2: Class, right-aligned.
- Row 2, col 1: Location (muted, ellipsized).
- Row 2, col 2: Length, right-aligned, muted.
- Hidden cells on phone: River (`secondary`), Status text (dot
  encodes it).
- The whole row remains a real `<tr>`, just with `display:grid`.
  Cells stay in DOM order so desktop view (table-cell) keeps
  working without re-rendering. ARIA table semantics break under
  `display:grid` — same accepted tradeoff as `table.levels` card
  at `style.css:158`.

### Tablet / landscape phone (640-1023px)

Mirror `table.levels`' tablet rules at
`src/kayak/web/static/style.css:235-246`:

- Keep table view (no card grid).
- Cap `td[data-label="Location"]` at `22ch` with ellipsis so
  long descriptions don't push Status off-screen.
- All six columns remain visible.

### Desktop (1024+)

No change. Current full-width table.

### Implementation steps

1. **PHP** — `php/includes/gauge_detail.php:565-583`:
   - Change `<table class="readings-table">` to
     `<table class="readings-table assoc-reaches">`.
   - Wrap the header `<tr>` in `<thead>...</thead>` and the data
     rows in `<tbody>...</tbody>` (iter 3 (A)) so card mode can
     hide the header via `thead{display:none}`.
   - Add `data-status="<?= htmlspecialchars($status) ?>"` to each
     row `<tr>` (iter 3 (D)) so the dot-color CSS can match.
   - Add per-cell `class` + `data-label`:
     - Name → `class="td-name" data-label="Name"` (the `<a>` tag
       stays inside).
     - Location → `data-label="Location"`.
     - River → `class="secondary" data-label="River"` so phone
       CSS can hide it with `td.secondary[data-label="River"]
       {display:none}`.
     - Class → `data-label="Class"`.
     - Length → `data-label="Length"`.
     - Status → `class="td-status" data-label="Status"`.

2. **CSS** — `src/kayak/web/static/style.css`, new block after
   `:285`:
   - `@media(max-width:639px)` scoped to
     `.readings-table.assoc-reaches`:
     - `thead{display:none}` (hide header row — iter 3 (A)).
     - `tbody, tr` → `display:block` / `display:grid` (card
       layout).
     - `td{padding:0; border:none}` (reset base-rule lattice —
       iter 3 (B)).
     - `td.td-name a{display:inline; color:var(--c-text);
       text-decoration:none}` (inline tap target — iter 3 (C)).
     - Hide River cell + Status text cell:
       `td.secondary[data-label="River"], td.td-status{
       display:none}`.
     - Status dot via
       `.readings-table.assoc-reaches tr[data-status="low|okay|high|unknown"]
       td.td-name::before` (same pattern as
       `style.css:223-228`).
   - `@media(min-width:640px) and (max-width:1023px)`: cap
     Location to `22ch` with ellipsis (mirrors `:241-245`).

3. **Test** — `tests/php/GaugeIntegrationTest.php:182-208`:
   - Extend the existing `assertResponseContains(...)` varargs at
     `:187-197` with one more substring:
     `'class="readings-table assoc-reaches"'`.
   - Existing `Clackamas Above Estacada` substring still passes
     because the reach name text is unchanged.

## Files affected

- `php/includes/gauge_detail.php` — `_render_associated_reaches()`
  (`:565` table class, `:582` row template). ~10 line diff.
- `src/kayak/web/static/style.css` — new block after `:285`. ~40
  lines.
- `tests/php/GaugeIntegrationTest.php` — one positive assertion.
  ~2 lines.

No JS change. No DB change. No build-pipeline change. No nginx
change. `levels build` republishes the changed CSS + PHP
includes; mtime-based cache-buster handles browser cache.

## Edge cases

- **Empty Location** (some reaches have `description=NULL`,
  including the integration-test fixture's "Clackamas Above
  Estacada"): the PHP coalesces to `''` via
  `htmlspecialchars((string)($r['description'] ?? ''))`. With
  iter 3 (B)'s `td{padding:0; border:none}` reset in card mode,
  an empty cell contributes zero height — row 2 col 1 just sits
  blank while Length renders on col 2. Self-handling, no PHP join
  logic needed.
- **Empty Length** (legacy reaches without measured length):
  same — empty col 2 collapses to zero width inside the `auto`
  track.
- **Empty River**: hidden on phone anyway via the `.secondary`
  display:none rule; tablet view still renders the (possibly
  empty) cell, matching the existing levels-table tablet pattern.
- **Long names** (`aw_<id>` placeholders, or fully spelled-out
  river+section): apply `overflow:hidden; text-overflow:ellipsis`
  on the name cell to prevent push-through.
- **Status="unknown"**: keep the dot in `var(--c-text-muted)`
  (existing `tr[data-status]::before` rule's default). Sanity-
  check against `style.css:223-225` — generic
  `tr[data-status] td.td-name::before` uses muted; specific
  `low|okay|high` selectors override.

## Testing approach

- **Local PHPUnit + PHPStan + CS Fixer**:
  ```
  composer test
  composer analyse
  composer fix-check
  ```
  Expect: no regressions, new positive assertion passes.
- **In-browser, levels-test.wkcc.org**:
  - **Phone portrait (~390 px)** — `/gauge.php?id=14` (Three
    Lynx, ~7 reaches): card view applies, status dot visible,
    Class on right, Location · Length on row 2. Status column
    no longer overflows.
  - **Phone portrait, gauge with 1 reach** — pick any
    single-reach gauge. Card still renders cleanly.
  - **Phone portrait, gauge with 0 reaches** — `/gauge.php?id=N`
    with no associated reaches: "No associated reaches"
    paragraph shows, no table emitted (current behavior).
  - **Landscape phone / small tablet (~640-767 px)** — table
    view, Location capped to 22ch with ellipsis.
  - **iPad portrait (~768 px)** — table view, all 6 cols.
  - **Desktop (1024+ px)** — table view, unchanged.
- **Visual regression** — the four other `.readings-table`
  consumers (gauge readings on `/gauge.php?id=N`, associated
  sources on same, description readings on
  `/description.php?id=N`, data export on `/data.php?id=N`)
  remain pixel-identical because none of them carry the
  `assoc-reaches` class.

## Risk

Low. Class-scoped CSS, no JS, no DB, no build-pipeline change.
The other four `.readings-table` consumers are isolated by the
class scope. Worst case the card layout needs spacing tweaks —
quick CSS edit.

## Decisions (settled across iter 1-5, pending user approval)

All five v1 decisions settled at the recommended value during the
iter loop. Each remains tunable if the chosen value misbehaves
in-browser.

1. **Drop Status text column on phone** — the `tr[data-status]`
   dot-before-name already encodes status visually; the textual
   word is redundant. `td.td-status{display:none}` in card mode.
2. **Drop River column on phone** — uniformly constant across
   rows on most per-gauge pages (confirmed iter-3 (B) of
   `PLAN_map_and_ui_tweaks.md` against `gauge.php?id=14`).
   `td.secondary[data-label="River"]{display:none}` in card mode.
3. **Show Length on phone** — on row 2 col 2, right-aligned and
   muted. Useful at-a-glance for picking a run by length.
4. **Show Class on phone** — on row 1 col 2, right-aligned. Class
   is the strongest non-status signal a kayaker uses when
   scanning options.
5. **Card breakpoint** — `max-width:639px` (mirrors
   `table.levels` card threshold). Tablet (640-1023 px) keeps
   table view + `td[data-label="Location"]{max-width:22ch}`
   ellipsis cap. Desktop (1024+ px) unchanged.
