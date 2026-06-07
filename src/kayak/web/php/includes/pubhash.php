<?php
declare(strict_types=1);
/**
 * Base-62 codec for public id <-> handle conversion — the PHP mirror of
 * src/kayak/utils/pubhash.py.
 *
 * In the metadata-single-source redesign the numeric primary key `id` is
 * stable and author-assigned, never a prod autoincrement. `pubhash_encode($id)`
 * is the short, opaque handle that appears in public URLs; the entry-point
 * handlers `pubhash_decode($handle)` back to the id and look the row up by
 * `WHERE id = ?` (internal joins stay integer).
 *
 * Alphabet `[0-9 a-z A-Z]` (62 chars), case-sensitive: URL query strings and
 * SQLite's BINARY collation both preserve case, so `aB` and `ab` are distinct
 * handles for distinct ids. Encoding is 1-based — the smallest id (1) is "1",
 * never the PHP-falsy "0" — and `pubhash_encode()` rejects id < 1.
 *
 * The two implementations MUST stay byte-identical: tests/test_pubhash.py and
 * tests/php/PubhashTest.php assert the same vectors against each side, so a
 * handle minted by the Python build decodes to the same id in PHP and back.
 *
 * No mbstring (absent in prod PHP-FPM): the alphabet is ASCII, so strlen /
 * substr / strpos index by byte == by character.
 */

const PUBHASH_ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
const PUBHASH_BASE = 62;

/**
 * Encode a positive integer id as its canonical base-62 handle.
 *
 * @throws InvalidArgumentException when $id < 1 (0 would encode to the falsy
 *                                  "0"; negative ids do not exist).
 */
function pubhash_encode(int $id): string
{
    if ($id < 1) {
        throw new InvalidArgumentException("pubhash_encode: id must be >= 1, got {$id}");
    }
    $out = '';
    while ($id > 0) {
        $out = PUBHASH_ALPHABET[$id % PUBHASH_BASE] . $out;
        $id = intdiv($id, PUBHASH_BASE);
    }
    return $out;
}

/**
 * Decode a base-62 handle back to its integer id.
 *
 * Lenient on leading zeros to match the Python side (decode("01") == 1);
 * re-encode the result if you need to reject non-canonical aliases.
 *
 * @throws InvalidArgumentException on an empty handle, a character outside the
 *                                  alphabet, or a handle long enough to
 *                                  overflow PHP's signed int.
 */
function pubhash_decode(string $handle): int
{
    if ($handle === '') {
        throw new InvalidArgumentException('pubhash_decode: empty handle');
    }
    $n = 0;
    $len = strlen($handle);
    for ($i = 0; $i < $len; $i++) {
        $pos = strpos(PUBHASH_ALPHABET, $handle[$i]);
        if ($pos === false) {
            throw new InvalidArgumentException('pubhash_decode: character outside the base-62 alphabet');
        }
        if ($n > intdiv(PHP_INT_MAX - $pos, PUBHASH_BASE)) {
            throw new InvalidArgumentException('pubhash_decode: handle overflows int');
        }
        $n = $n * PUBHASH_BASE + $pos;
    }
    return $n;
}
