"""Calculator for synthetic/derived gage readings (replaces calculator.C).

Evaluates calc_expr expressions that reference other stations' Latest values
to produce derived measurements.

Expression format: "hash1::key1::type1 + hash2::key2::type2"
where hash is the station hash, key is the station name, type is flow/gage/etc.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import click

from kayak.db.data_db import get_latest, store_measurement, update_latest
from kayak.db.engine import get_session
from kayak.db.models import DataType, MergedMaster

logger = logging.getLogger(__name__)

# Regex to match hash::name::type references in expressions
_REF_PATTERN = re.compile(r"(\w+)::\w+::(\w+)")


@click.command("calculator")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
def calculator_cmd(verbose):
    """Build synthetic/calculated gage readings from expressions."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    session = get_session()
    try:
        # Find stations with calculation expressions
        records = (
            session.query(MergedMaster)
            .filter(
                MergedMaster.calc_time.isnot(None),
                MergedMaster.calc_time != "",
                MergedMaster.calc_expr.isnot(None),
                MergedMaster.calc_expr != "",
                MergedMaster.calc_type.isnot(None),
                MergedMaster.calc_type != "",
                MergedMaster.db_name.isnot(None),
                MergedMaster.db_name != "",
            )
            .all()
        )

        click.echo(f"Found {len(records)} calculated stations")

        # Build hash -> db_name lookup
        all_records = session.query(MergedMaster).filter(
            MergedMaster.db_name.isnot(None)
        ).all()
        hash_to_db = {r.hash_value: r.db_name for r in all_records}

        for record in records:
            try:
                calc_time_refs = record.calc_time.split()
                calc_expr = record.calc_expr
                calc_type = record.calc_type
                db_name = record.db_name

                if verbose:
                    click.echo(
                        f"  Calculating {db_name}: type={calc_type} "
                        f"expr={calc_expr}"
                    )

                # Resolve all references to actual values
                values = {}
                times = []
                skip = False

                for ref in calc_time_refs:
                    # ref format: hash::name::type
                    parts = ref.split("::")
                    if len(parts) != 3:
                        logger.error("Invalid ref format: %s", ref)
                        skip = True
                        break

                    ref_hash, ref_name, ref_type = parts
                    ref_db = hash_to_db.get(ref_hash)
                    if not ref_db:
                        logger.error("No db_name for hash %s", ref_hash)
                        skip = True
                        break

                    try:
                        ref_dtype = DataType(ref_type)
                    except ValueError:
                        logger.error("Unknown type: %s", ref_type)
                        skip = True
                        break

                    latest = get_latest(session, ref_db, ref_dtype)
                    if latest is None or latest.value is None:
                        logger.warning(
                            "No latest value for %s/%s", ref_db, ref_type
                        )
                        skip = True
                        break

                    values[ref] = latest.value
                    if latest.time:
                        times.append(latest.time)

                if skip or not times:
                    continue

                # Use the earliest time from all references
                when = min(times)

                # Evaluate the expression by substituting values
                expr = calc_expr
                for ref, val in values.items():
                    expr = expr.replace(ref, str(val))

                # Clean up SQL functions for Python eval
                expr = expr.replace("round(", "round(")
                expr = expr.replace("greatest(", "max(")
                expr = expr.replace("least(", "min(")

                try:
                    result = eval(expr)  # noqa: S307 — expressions come from DB seed data
                except Exception as e:
                    logger.error("Error evaluating '%s': %s", expr, e)
                    continue

                result = max(0, float(result))

                try:
                    dtype = DataType(calc_type)
                except ValueError:
                    logger.error("Unknown calc_type: %s", calc_type)
                    continue

                if store_measurement(session, db_name, dtype, when, result):
                    update_latest(session, db_name, dtype)
                    if verbose:
                        click.echo(f"    = {result:.1f} at {when}")

            except Exception as e:
                click.echo(f"  Error for {record.db_name}: {e}", err=True)

        session.commit()
        click.echo("Calculations complete")
    finally:
        session.close()
