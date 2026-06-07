<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/reach_fields.php';

/**
 * Unit tests for the consolidated reach-detail formatters shared by
 * description.php and reach.php. Pure functions — no DB, no HTTP.
 */
final class ReachFieldsTest extends TestCase
{
    // --- format_reach_watershed -------------------------------------------

    public function test_watershed_basin_state_region(): void
    {
        $this->assertSame(
            'Wilson in Oregon, North Coast',
            format_reach_watershed(['basin' => 'Wilson', 'region' => 'North Coast'], ['Oregon'])
        );
    }

    public function test_watershed_multiple_states_no_region(): void
    {
        $this->assertSame(
            'Snake in Oregon, Idaho',
            format_reach_watershed(['basin' => 'Snake'], ['Oregon', 'Idaho'])
        );
    }

    public function test_watershed_states_only(): void
    {
        $this->assertSame('Oregon, Washington', format_reach_watershed([], ['Oregon', 'Washington']));
    }

    public function test_watershed_region_only(): void
    {
        $this->assertSame('Cascades', format_reach_watershed(['region' => 'Cascades'], []));
    }

    public function test_watershed_all_empty_returns_null(): void
    {
        $this->assertNull(format_reach_watershed([], []));
        $this->assertNull(format_reach_watershed(['basin' => '  ', 'region' => ''], []));
    }

    // --- format_reach_length ----------------------------------------------

    public function test_length_with_gradient_and_max(): void
    {
        $this->assertSame(
            '22.7 mi, gradient 11 ft/mi, max 29 ft/mi',
            format_reach_length(['length' => 22.7, 'gradient' => 11.0, 'max_gradient' => 29.0])
        );
    }

    public function test_length_only(): void
    {
        $this->assertSame('5.0 mi', format_reach_length(['length' => 5.0]));
    }

    public function test_length_null_or_zero_returns_null(): void
    {
        $this->assertNull(format_reach_length(['length' => null]));
        $this->assertNull(format_reach_length(['length' => 0]));
        $this->assertNull(format_reach_length([]));
    }

    // --- format_reach_elevation -------------------------------------------

    public function test_elevation_start_and_loss(): void
    {
        $this->assertSame(
            '241 ft to 2 ft, loss 239 ft',
            format_reach_elevation(['elevation' => 241.0, 'elevation_lost' => 239.0])
        );
    }

    public function test_elevation_start_only(): void
    {
        $this->assertSame('241 ft', format_reach_elevation(['elevation' => 241.0]));
    }

    public function test_elevation_loss_only(): void
    {
        $this->assertSame('loss 239 ft', format_reach_elevation(['elevation_lost' => 239.0]));
    }

    public function test_elevation_none_returns_null(): void
    {
        $this->assertNull(format_reach_elevation([]));
    }

    // --- format_reach_flow ------------------------------------------------

    public function test_flow_low_and_high_cfs(): void
    {
        $levels = [['level' => 'okay', 'low' => 400.0, 'high' => 2000.0]];
        $this->assertSame('low 400 CFS, high 2,000 CFS', format_reach_flow($levels));
    }

    public function test_flow_only_low(): void
    {
        $levels = [['level' => 'okay', 'low' => 400.0, 'high' => null]];
        $this->assertSame('low 400 CFS', format_reach_flow($levels));
    }

    public function test_flow_only_high(): void
    {
        $levels = [['level' => 'okay', 'low' => null, 'high' => 1500.0]];
        $this->assertSame('high 1,500 CFS', format_reach_flow($levels));
    }

    public function test_flow_gauge_height_units(): void
    {
        // Non-'flow' data type → gage height, "ft" with one decimal.
        $levels = [[
            'level' => 'okay',
            'low' => 3.5, 'low_data_type' => 'gauge',
            'high' => 8.0, 'high_data_type' => 'gauge',
        ]];
        $this->assertSame('low 3.5 ft, high 8.0 ft', format_reach_flow($levels));
    }

    public function test_flow_no_okay_band_returns_null(): void
    {
        $this->assertNull(format_reach_flow([['level' => 'low', 'low' => 100.0]]));
        $this->assertNull(format_reach_flow([]));
    }
}
