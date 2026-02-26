"""Click CLI entry point (replaces individual C++ programs)."""

import click


@click.group()
@click.version_option(version="0.1.0", prog_name="kayak")
def cli():
    """Kayak - River level data aggregation from government agencies."""


# Import and register all subcommands
from kayak.cli.build import build_cmd  # noqa: E402
from kayak.cli.calc_rating import calc_rating_cmd  # noqa: E402
from kayak.cli.calculator import calculator_cmd  # noqa: E402
from kayak.cli.fetch import fetch_cmd  # noqa: E402
from kayak.cli.init_db import init_db  # noqa: E402
from kayak.cli.merge import merge_cmd  # noqa: E402
from kayak.cli.pipeline import pipeline_cmd  # noqa: E402

cli.add_command(init_db)
cli.add_command(fetch_cmd)
cli.add_command(merge_cmd)
cli.add_command(calc_rating_cmd)
cli.add_command(calculator_cmd)
cli.add_command(build_cmd)
cli.add_command(pipeline_cmd)
