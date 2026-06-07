<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Unit tests for the URL-validation primitives in php/includes/auth.php.
 * Pure functions — no DB, no HTTP.
 */
final class SanityTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        // auth.php transitively loads db.php which defines get_db() but
        // does NOT call it at load time — safe to include. Tests here
        // exercise pure helpers that don't touch the DB at all.
        require_once __DIR__ . '/../../src/kayak/web/php/includes/auth.php';
        require_once __DIR__ . '/../../src/kayak/web/php/includes/sanity.php';
    }

    public function testNullReturnsRoot(): void
    {
        $this->assertSame('/', safe_next_url(null));
    }

    public function testEmptyReturnsRoot(): void
    {
        $this->assertSame('/', safe_next_url(''));
    }

    public function testSameOriginPathIsAccepted(): void
    {
        $this->assertSame('/reach.php?id=42', safe_next_url('/reach.php?id=42'));
    }

    public function testProtocolRelativeRejected(): void
    {
        // //evil.example/ is protocol-relative — browsers would send the
        // user off-site. Must not survive validation.
        $this->assertSame('/', safe_next_url('//evil.example/pwn'));
    }

    public function testBackslashSecondCharRejected(): void
    {
        // Per the WHATWG URL spec, browsers normalize `\` to `/` in
        // special-scheme URLs, so `/\evil.example/` is rendered as
        // `//evil.example/` and redirects cross-origin.
        $this->assertSame('/', safe_next_url('/\\evil.example/'));
        $this->assertSame('/', safe_next_url('/\\\\evil.example/'));
    }

    public function testAbsoluteHttpsRejected(): void
    {
        $this->assertSame('/', safe_next_url('https://evil.example/'));
    }

    public function testJavascriptSchemeRejected(): void
    {
        $this->assertSame('/', safe_next_url('javascript:alert(1)'));
    }

    public function testRelativePathWithoutLeadingSlashRejected(): void
    {
        $this->assertSame('/', safe_next_url('reach.php?id=42'));
    }

    // -----------------------------------------------------------------
    // strip_html_tags — preserves user text, strips real tags
    // -----------------------------------------------------------------

    public function testStripTagsPlainText(): void
    {
        $this->assertSame('plain text', strip_html_tags('plain text'));
    }

    public function testStripTagsPreservesLessThanThree(): void
    {
        // Native PHP strip_tags would eat everything after "<".
        $this->assertSame('I love <3 boats', strip_html_tags('I love <3 boats'));
        $this->assertSame('<3', strip_html_tags('<3'));
    }

    public function testStripTagsPreservesInequalities(): void
    {
        $this->assertSame('x < y and y > z', strip_html_tags('x < y and y > z'));
    }

    public function testStripTagsPreservesBracketedEmail(): void
    {
        // "Name <foo@bar.com>" convention — @ isn't whitespace, so the run
        // after the tag name doesn't match, and the sequence is preserved.
        $this->assertSame('<foo@bar.com>', strip_html_tags('<foo@bar.com>'));
    }

    public function testStripTagsRemovesScript(): void
    {
        $this->assertSame('alert(1)', strip_html_tags('<script>alert(1)</script>'));
    }

    public function testStripTagsRemovesNestedReassembly(): void
    {
        // After one pass this would become "<script>alert(1)</script>" — the
        // loop re-strips until stable so the payload can't sneak through.
        $input = '<scr<script>ipt>alert(1)</scr</script>ipt>';
        $this->assertSame('alert(1)', strip_html_tags($input));
    }

    public function testStripTagsRemovesAttributes(): void
    {
        $this->assertSame('Hello', strip_html_tags('<p class="x">Hello</p>'));
        $this->assertSame('link', strip_html_tags('<a href="https://example.com">link</a>'));
    }

    public function testStripTagsRemovesComments(): void
    {
        $this->assertSame('visible', strip_html_tags('<!-- evil --> visible'));
    }

    public function testStripTagsPreservesUnclosedLessThan(): void
    {
        // No `>` terminator → not a tag, keep verbatim.
        $this->assertSame('unclosed <tag text', strip_html_tags('unclosed <tag text'));
    }

    public function testStripTagsMixedCaseTagname(): void
    {
        $this->assertSame('MIXED case', strip_html_tags('<TAG>MIXED case</TAG>'));
    }

    public function testStripTagsMultilineAttributes(): void
    {
        $this->assertSame('link', strip_html_tags("<a\n href=\"x\">link</a>"));
    }

    public function testStripTagsTrimsWhitespace(): void
    {
        $this->assertSame('trim me', strip_html_tags('   <b>trim me</b>   '));
    }

    // -----------------------------------------------------------------
    // normalize_name
    // -----------------------------------------------------------------

    public function testNormalizeNameLowercasesAndStrips(): void
    {
        // "River"/"Creek" hydronyms drop out; punctuation → space; collapse ws.
        $this->assertSame('wilson', normalize_name('Wilson River'));
        $this->assertSame('clackamas', normalize_name('Clackamas Creek'));
    }

    public function testNormalizeNameDropsStopwordsAndPunctuation(): void
    {
        $this->assertSame('nf clackamas', normalize_name('NF of the Clackamas!'));
        $this->assertSame('snake', normalize_name('  Snake   River  '));
    }

    // -----------------------------------------------------------------
    // check_display_name
    // -----------------------------------------------------------------

    public function testDisplayNameEmptyIsError(): void
    {
        $issues = check_display_name('   ', null);
        $this->assertCount(1, $issues);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertSame('display_name', $issues[0]['field']);
    }

    public function testDisplayNameTooLongIsError(): void
    {
        $issues = check_display_name(str_repeat('x', 129), null);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('128 characters', $issues[0]['message']);
    }

    public function testDisplayNameMustIncludeRiver(): void
    {
        // Proposed name doesn't contain the (normalized) river → error.
        $issues = check_display_name('Some Other Run', 'Wilson');
        $this->assertCount(1, $issues);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('Wilson', $issues[0]['message']);
    }

    public function testDisplayNameContainingRiverIsAccepted(): void
    {
        // Prefixes are fine as long as the river name appears.
        $this->assertSame([], check_display_name('NF Wilson River', 'Wilson'));
        $this->assertSame([], check_display_name('Upper Clackamas', 'Clackamas River'));
    }

    public function testDisplayNameNoRiverConstraintWhenRiverNullOrBlank(): void
    {
        $this->assertSame([], check_display_name('Anything Goes', null));
        $this->assertSame([], check_display_name('Anything Goes', '   '));
    }

    // -----------------------------------------------------------------
    // check_text_length
    // -----------------------------------------------------------------

    public function testTextLengthWithinLimitIsOk(): void
    {
        $this->assertSame([], check_text_length('notes', 'short', 100));
        $this->assertSame([], check_text_length('notes', str_repeat('a', 100), 100)); // boundary
    }

    public function testTextLengthOverLimitIsError(): void
    {
        $issues = check_text_length('notes', str_repeat('a', 101), 100);
        $this->assertCount(1, $issues);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertSame('notes', $issues[0]['field']);
        $this->assertStringContainsString('got 101', $issues[0]['message']);
    }

    // -----------------------------------------------------------------
    // check_class_string
    // -----------------------------------------------------------------

    public function testClassStringEmptyIsError(): void
    {
        $issues = check_class_string('class', '  ');
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('cannot be empty', $issues[0]['message']);
    }

    public function testClassStringTooLongIsError(): void
    {
        $issues = check_class_string('class', str_repeat('I', 33));
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('32 characters', $issues[0]['message']);
    }

    public function testClassStringValidFormatsAccepted(): void
    {
        foreach (['III', 'III+', 'II-III', 'IV V', 'III+(IV)', 'V.1'] as $v) {
            $this->assertSame([], check_class_string('class', $v), "expected '$v' to be accepted");
        }
    }

    public function testClassStringUnexpectedFormatIsWarning(): void
    {
        // Doesn't match the class grammar → soft warning, not a hard error.
        $issues = check_class_string('class', 'Grade 6');
        $this->assertCount(1, $issues);
        $this->assertSame('warning', $issues[0]['level']);
    }

    // -----------------------------------------------------------------
    // check_flow_range
    // -----------------------------------------------------------------

    public function testFlowRangeValidIsEmpty(): void
    {
        $this->assertSame([], check_flow_range(400.0, 2000.0, 'flow'));
        $this->assertSame([], check_flow_range(null, null, 'flow'));
        $this->assertSame([], check_flow_range(3.5, 8.0, 'gauge'));
    }

    public function testFlowRangeLowAboveHighIsError(): void
    {
        $issues = check_flow_range(2000.0, 400.0, 'flow');
        $errs = array_filter($issues, fn($i) => $i['level'] === 'error');
        $this->assertNotEmpty($errs);
    }

    public function testFlowRangeOutOfBoundsIsWarning(): void
    {
        // 300000 CFS exceeds the 200000 ceiling → warning (not blocking).
        $issues = check_flow_range(300000.0, null, 'flow');
        $this->assertCount(1, $issues);
        $this->assertSame('warning', $issues[0]['level']);
    }

    public function testFlowRangeGaugeNegativeOutOfRange(): void
    {
        // gauge range is [-20, 100]; -30 is below it.
        $issues = check_flow_range(-30.0, null, 'gauge');
        $this->assertSame('warning', $issues[0]['level']);
    }

    public function testFlowRangeUnknownTypeUsesDefaultBounds(): void
    {
        // Unknown data_type → falls back to [0, 200000]; 500 is fine.
        $this->assertSame([], check_flow_range(500.0, null, 'mystery'));
    }

    // -----------------------------------------------------------------
    // check_coords (+ _haversine_mi)
    // -----------------------------------------------------------------

    public function testCoordsBothNullIsEmpty(): void
    {
        $this->assertSame([], check_coords('putin', null, null));
    }

    public function testCoordsOnlyOneSuppliedIsError(): void
    {
        $issues = check_coords('putin', 44.0, null);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('both latitude and longitude', $issues[0]['message']);
    }

    public function testCoordsLatOutOfRangeIsError(): void
    {
        $issues = check_coords('putin', 95.0, -122.0);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('latitude', $issues[0]['message']);
    }

    public function testCoordsLonOutOfRangeIsError(): void
    {
        $issues = check_coords('putin', 44.0, -200.0);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('longitude', $issues[0]['message']);
    }

    public function testCoordsValidNoRefIsEmpty(): void
    {
        $this->assertSame([], check_coords('putin', 44.05, -122.0));
    }

    public function testCoordsNearReferenceIsEmpty(): void
    {
        // Same point as the reference → 0 mi, no issue.
        $this->assertSame([], check_coords('putin', 44.05, -122.0, 44.05, -122.0));
    }

    public function testCoordsModeratelyFarIsWarning(): void
    {
        // ~14 mi north of the reference (1 deg lat ~= 69 mi → 0.2 deg ~= 14 mi):
        // between 10 and 100 → warning.
        $issues = check_coords('putin', 44.25, -122.0, 44.05, -122.0);
        $this->assertCount(1, $issues);
        $this->assertSame('warning', $issues[0]['level']);
        $this->assertStringContainsString('from the current location', $issues[0]['message']);
    }

    public function testCoordsVeryFarIsError(): void
    {
        // ~2 deg lat ~= 138 mi from the reference → over 100 → error.
        $issues = check_coords('putin', 46.05, -122.0, 44.05, -122.0);
        $this->assertCount(1, $issues);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('refusing', $issues[0]['message']);
    }

    // -----------------------------------------------------------------
    // check_putin_takeout
    // -----------------------------------------------------------------

    public function testPutinTakeoutMissingCoordsIsEmpty(): void
    {
        $this->assertSame([], check_putin_takeout(44.0, -122.0, null, null));
        $this->assertSame([], check_putin_takeout(null, null, 44.0, -122.0));
    }

    public function testPutinTakeoutShortRunIsEmpty(): void
    {
        // A few miles apart — a normal reach length, no issue.
        $this->assertSame([], check_putin_takeout(44.05, -122.0, 44.10, -122.0));
    }

    public function testPutinTakeoutLongIsWarning(): void
    {
        // ~1 deg lat ~= 69 mi — between 60 and 200 → warning.
        $issues = check_putin_takeout(44.0, -122.0, 45.0, -122.0);
        $this->assertCount(1, $issues);
        $this->assertSame('warning', $issues[0]['level']);
    }

    public function testPutinTakeoutTooLongIsError(): void
    {
        // ~4 deg lat ~= 276 mi — over 200 → error.
        $issues = check_putin_takeout(44.0, -122.0, 48.0, -122.0);
        $this->assertCount(1, $issues);
        $this->assertSame('error', $issues[0]['level']);
        $this->assertStringContainsString('too long', $issues[0]['message']);
    }

    // -----------------------------------------------------------------
    // sanity_errors / sanity_warnings
    // -----------------------------------------------------------------

    public function testSanityErrorsAndWarningsPartition(): void
    {
        $issues = [
            ['level' => 'error',   'field' => 'a', 'message' => 'e1'],
            ['level' => 'warning', 'field' => 'b', 'message' => 'w1'],
            ['level' => 'error',   'field' => 'c', 'message' => 'e2'],
        ];
        $errors = sanity_errors($issues);
        $warnings = sanity_warnings($issues);
        $this->assertCount(2, $errors);
        $this->assertCount(1, $warnings);
        // array_values reindexes — list, not a sparse map.
        $this->assertSame([0, 1], array_keys($errors));
        $this->assertSame('w1', $warnings[0]['message']);
    }

    public function testSanityErrorsWarningsEmptyInput(): void
    {
        $this->assertSame([], sanity_errors([]));
        $this->assertSame([], sanity_warnings([]));
    }
}
