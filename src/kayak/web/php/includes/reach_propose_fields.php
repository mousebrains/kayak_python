<?php
declare(strict_types=1);
/**
 * The reach columns an editor proposal may carry, by tier — the single source
 * of truth shared by the propose form and the maintainer review-apply path:
 *
 *   - propose_handler.php splits them by editor tier when building the form +
 *     the change_request payload.
 *   - review_logic.php intersects the stored payload_json keys against the
 *     union (array_merge of both) before composing `UPDATE reach SET <col> = ?`,
 *     because the column name is interpolated as a SQL identifier (not bindable).
 *     A tampered payload_json therefore cannot inject a column name. review-4 R1.4.
 *
 * Keeping both consumers on these two constants prevents the propose set and the
 * apply allowlist from drifting apart. (No union constant: array unpacking is not
 * a valid constant expression, so the union is array_merge()'d at the call site.)
 */

/** Reach text fields any signed-in editor may propose. */
const REACH_TEXT_FIELDS = ['description', 'features'];

/** Additional reach fields only full/maintainer editors may propose. */
const REACH_FULL_FIELDS = [
    'display_name',
    'latitude_start', 'longitude_start',
    'latitude_end',   'longitude_end',
];
