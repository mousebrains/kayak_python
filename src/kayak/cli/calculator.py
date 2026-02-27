"""Calculator for synthetic/derived gage readings (replaces calculator.C).

Evaluates CalcExpression entries that reference other sources' Latest values
to produce derived observations.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone

from kayak.db.data_db import get_latest, store_observation, update_latest
from kayak.db.engine import get_session
from kayak.db.models import CalcExpression, DataType, Source

logger = logging.getLogger(__name__)


def addArgs(subparsers):
    """Register the 'calculator' subcommand."""
    parser = subparsers.add_parser("calculator",
                                   help="Build synthetic/calculated gage readings from expressions")
    parser.set_defaults(func=calculator)


def calculator(args):
    """Build synthetic/calculated gage readings from expressions."""

    session = get_session()
    try:
        # Find sources with calculation expressions
        calc_sources = (
            session.query(Source)
            .filter(Source.calc_expression_id.isnot(None))
            .all()
        )

        print(f"Found {len(calc_sources)} calculated sources")

        # Build source name -> id lookup
        all_sources = session.query(Source).all()
        name_to_id = {s.name: s.id for s in all_sources}

        for source in calc_sources:
            try:
                calc_expr = source.calc_expression
                if calc_expr is None:
                    continue

                expression = calc_expr.expression
                time_expression = calc_expr.time_expression
                data_type = calc_expr.data_type

                logger.info(
                    "Calculating %s: type=%s expr=%s",
                    source.name, data_type.value, expression,
                )

                if not time_expression:
                    logger.warning("No time_expression for source %s", source.name)
                    continue

                # Resolve all references to actual values
                # time_expression contains source references like "source_name::type"
                values = {}
                times = []
                skip = False

                for ref in time_expression.split():
                    parts = ref.split("::")
                    if len(parts) < 2:
                        logger.error("Invalid ref format: %s", ref)
                        skip = True
                        break

                    ref_name = parts[0]
                    ref_type_str = parts[1] if len(parts) > 1 else data_type.value

                    ref_source_id = name_to_id.get(ref_name)
                    if not ref_source_id:
                        logger.error("No source_id for name %s", ref_name)
                        skip = True
                        break

                    try:
                        ref_dtype = DataType(ref_type_str)
                    except ValueError:
                        logger.error("Unknown type: %s", ref_type_str)
                        skip = True
                        break

                    latest = get_latest(session, ref_source_id, ref_dtype)
                    if latest is None or latest.value is None:
                        logger.warning(
                            "No latest value for %s/%s", ref_name, ref_type_str
                        )
                        skip = True
                        break

                    values[ref] = latest.value
                    times.append(latest.observed_at)

                if skip or not times:
                    continue

                # Use the earliest time from all references
                when = min(times)

                # Evaluate the expression by substituting values
                expr = expression
                for ref, val in values.items():
                    expr = expr.replace(ref, str(val))

                # Clean up SQL functions for Python eval
                expr = expr.replace("greatest(", "max(")
                expr = expr.replace("least(", "min(")

                try:
                    result = eval(expr)  # noqa: S307 — expressions come from DB seed data
                except Exception as e:
                    logger.error("Error evaluating '%s': %s", expr, e)
                    continue

                result = max(0, float(result))

                if store_observation(session, source.id, data_type, when, result):
                    update_latest(session, source.id, data_type)
                    logger.debug("  = %.1f at %s", result, when)

            except Exception as e:
                print(f"  Error for {source.name}: {e}", file=sys.stderr)

        session.commit()
        print("Calculations complete")
    finally:
        session.close()
