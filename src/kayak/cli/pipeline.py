"""Pipeline orchestrator (replaces scripts/master).

Runs the full data pipeline in order:
1. fetch — fetch from remote agencies
2. calc-rating — apply rating tables
3. merge — merge multi-source data
4. calculator — compute derived values
5. build — generate output pages
"""

from __future__ import annotations

import logging
import time

import click


@click.command("pipeline")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
@click.option("--skip-fetch", is_flag=True, help="Skip the fetch step")
@click.option("--dry-run", is_flag=True, help="Dry run (no DB writes)")
@click.pass_context
def pipeline_cmd(ctx, verbose, skip_fetch, dry_run):
    """Run the full data pipeline (fetch → calc-rating → merge → calculator → build)."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    steps = []

    if not skip_fetch:
        from kayak.cli.fetch import fetch_cmd
        steps.append(("fetch", fetch_cmd, {"verbose": verbose, "dry_run": dry_run}))

    from kayak.cli.calc_rating import calc_rating_cmd
    from kayak.cli.merge import merge_cmd
    from kayak.cli.calculator import calculator_cmd
    from kayak.cli.build import build_cmd

    steps.extend([
        ("calc-rating", calc_rating_cmd, {"verbose": verbose}),
        ("merge", merge_cmd, {"verbose": verbose}),
        ("calculator", calculator_cmd, {"verbose": verbose}),
        ("build", build_cmd, {"verbose": verbose}),
    ])

    for step_name, cmd, kwargs in steps:
        click.echo(f"\n{'='*60}")
        click.echo(f"Running: {step_name}")
        click.echo(f"{'='*60}")
        start = time.time()
        try:
            ctx.invoke(cmd, **kwargs)
        except SystemExit:
            pass
        except Exception as e:
            click.echo(f"Error in {step_name}: {e}", err=True)
        elapsed = time.time() - start
        click.echo(f"Completed {step_name} in {elapsed:.1f}s")

    click.echo(f"\n{'='*60}")
    click.echo("Pipeline complete")
