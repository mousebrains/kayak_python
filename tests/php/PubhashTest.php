<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/pubhash.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/pubhash_request.php';

/**
 * Unit tests for php/includes/pubhash.php — the PHP half of the base-62
 * id <-> handle codec. The vectors mirror tests/test_pubhash.py so the two
 * implementations stay byte-identical: a handle minted by the Python build
 * must decode to the same id in the PHP entry points, and a handle minted in
 * PHP must decode to the same id in Python.
 */
final class PubhashTest extends TestCase
{
    public function testPubhashUrl(): void
    {
        $this->assertSame('/source.php?h=1', pubhash_url('source', 1));
        $this->assertSame('/gauge.php?h=' . pubhash_encode(140), pubhash_url('gauge', 140));
        // $extra is appended verbatim after the handle.
        $this->assertSame(
            '/source_plot.php?h=' . pubhash_encode(7) . '&type=flow&embed=1',
            pubhash_url('source_plot', 7, '&type=flow&embed=1'),
        );
    }

    public function testEncodeKnownValues(): void
    {
        $this->assertSame('1', pubhash_encode(1));
        $this->assertSame('9', pubhash_encode(9));
        $this->assertSame('a', pubhash_encode(10));
        $this->assertSame('z', pubhash_encode(35));
        $this->assertSame('A', pubhash_encode(36));
        $this->assertSame('Z', pubhash_encode(61));
        $this->assertSame('10', pubhash_encode(62));
        $this->assertSame('58', pubhash_encode(318)); // ~ a source-table count
    }

    public function testCaseSensitiveAlphabet(): void
    {
        // lower and upper are distinct values — the whole point of base-62.
        $this->assertSame('a', pubhash_encode(10));
        $this->assertSame('A', pubhash_encode(36));
        $this->assertSame(10, pubhash_decode('a'));
        $this->assertSame(36, pubhash_decode('A'));
        $this->assertNotSame(pubhash_decode('ab'), pubhash_decode('aB'));
    }

    public function testEncodeRejectsZero(): void
    {
        $this->expectException(InvalidArgumentException::class);
        pubhash_encode(0);
    }

    public function testEncodeRejectsNegative(): void
    {
        $this->expectException(InvalidArgumentException::class);
        pubhash_encode(-1);
    }

    public function testDecodeRoundTrips(): void
    {
        foreach ([1, 9, 10, 35, 36, 61, 62, 318, 3843, 3844, 999999] as $n) {
            $this->assertSame($n, pubhash_decode(pubhash_encode($n)));
        }
    }

    public function testDecodeRejectsEmpty(): void
    {
        $this->expectException(InvalidArgumentException::class);
        pubhash_decode('');
    }

    public function testDecodeRejectsBadCharacter(): void
    {
        $this->expectException(InvalidArgumentException::class);
        pubhash_decode('a-b');
    }

    public function testDecodeIsLenientOnLeadingZeros(): void
    {
        // documented: decode is lenient, encode is canonical; round-trip via
        // encode is the way to reject a non-canonical alias.
        $this->assertSame(1, pubhash_decode('01'));
        $this->assertSame(pubhash_decode('1'), pubhash_decode('01'));
        $this->assertSame('1', pubhash_encode(pubhash_decode('01')));
    }

    public function testEncodeNeverEmitsBareZero(): void
    {
        for ($n = 1; $n < 4000; $n++) {
            $this->assertNotSame('0', pubhash_encode($n));
        }
    }
}
