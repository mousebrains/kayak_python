"""Calculator for synthetic/derived gage readings (replaces calculator.C).

Evaluates CalcExpression entries that reference other sources' Latest values
to produce derived observations.
"""

from __future__ import annotations

import ast
import logging
import operator

from kayak.db.data_db import get_latest, store_observation, update_latest
from kayak.db.engine import get_session
from kayak.db.info_db import get_primary_source_id
from kayak.db.models import DataType, Gauge, Source

logger = logging.getLogger(__name__)

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

def _safe_round(value, ndigits=None):
    """round() wrapper that accepts float ndigits from the evaluator."""
    if ndigits is not None:
        ndigits = int(ndigits)
    return round(value, ndigits)

_SAFE_FUNCS = {"max": max, "min": min, "round": _safe_round}


def _safe_eval(expr: str) -> float:
    """Evaluate a simple arithmetic expression safely via AST.

    Supports: numeric constants, +, -, *, /, **, unary +/-, max(), min().
    Raises ValueError for any unsupported constructs.
    """
    tree = ast.parse(expr, mode="eval")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            op_fn = _BINOPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op_fn(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            op_fn = _UNARYOPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
            return op_fn(_eval(node.operand))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError(f"Unsupported call: {ast.dump(node.func)}")
            fn = _SAFE_FUNCS.get(node.func.id)
            if fn is None:
                raise ValueError(f"Unsupported function: {node.func.id}")
            return fn(*(_eval(arg) for arg in node.args))
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    return float(_eval(tree))


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

        # Build name -> source_id lookup from Source names and Gauge names
        all_sources = session.query(Source).all()
        name_to_id = {s.name: s.id for s in all_sources}

        # Also map gauge names to their primary source_id
        for gauge in session.query(Gauge).all():
            sid = get_primary_source_id(session, gauge.id)
            if sid and gauge.name not in name_to_id:
                name_to_id[gauge.name] = sid

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
                # time_expression refs are "key::source_name::type" (3-part)
                # or "source_name::type" (2-part)
                values = {}
                times = []
                skip = False

                for ref in time_expression.split():
                    parts = ref.split("::")
                    if len(parts) < 2:
                        logger.error("Invalid ref format: %s", ref)
                        skip = True
                        break

                    if len(parts) >= 3:
                        ref_name = parts[1]
                        ref_type_str = parts[2]
                    else:
                        ref_name = parts[0]
                        ref_type_str = parts[1]

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
                    result = _safe_eval(expr)
                except (ValueError, SyntaxError) as e:
                    logger.error("Error evaluating '%s': %s", expr, e)
                    continue

                result = max(0, float(result))

                if store_observation(session, source.id, data_type, when, result):
                    update_latest(session, source.id, data_type)
                    logger.debug("  = %.1f at %s", result, when)

            except Exception as e:
                logger.error("Error for %s: %s", source.name, e)

        session.commit()
        print("Calculations complete")
    finally:
        session.close()
