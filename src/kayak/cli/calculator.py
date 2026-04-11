"""Calculator for synthetic/derived gage readings (replaces calculator.C).

Evaluates CalcExpression entries that reference other sources' Latest values
to produce derived observations.
"""

from __future__ import annotations

import argparse
import ast
import logging
import operator
import re
from collections.abc import Callable

from kayak.db.data_db import get_latest_gauge, store_observation, update_latest, update_latest_gauge
from kayak.db.engine import get_session
from kayak.db.models import DataType, Gauge, GaugeSource, Source

logger = logging.getLogger(__name__)

_BINOPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

_UNARYOPS: dict[type, Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_round(value: float, ndigits: float | None = None) -> float:
    """round() wrapper that accepts float ndigits from the evaluator."""
    if ndigits is not None:
        ndigits = int(ndigits)
    return round(value, ndigits)


_SAFE_FUNCS: dict[str, Callable[..., float]] = {"max": max, "min": min, "round": _safe_round}


def _safe_eval(expr: str) -> float:
    """Evaluate a simple arithmetic expression safely via AST.

    Supports: numeric constants, +, -, *, /, **, unary +/-, max(), min().
    Raises ValueError for any unsupported constructs.
    """
    tree = ast.parse(expr, mode="eval")

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            bin_fn = _BINOPS.get(type(node.op))
            if bin_fn is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return bin_fn(_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            unary_fn = _UNARYOPS.get(type(node.op))
            if unary_fn is None:
                raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
            return unary_fn(_eval(node.operand))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError(f"Unsupported call: {ast.dump(node.func)}")
            fn = _SAFE_FUNCS.get(node.func.id)
            if fn is None:
                raise ValueError(f"Unsupported function: {node.func.id}")
            return fn(*(_eval(arg) for arg in node.args))
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    return float(_eval(tree))


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the 'calculator' subcommand."""
    parser = subparsers.add_parser(
        "calculator", help="Build synthetic/calculated gage readings from expressions"
    )
    parser.set_defaults(func=calculator)


def calculator(args: argparse.Namespace) -> None:
    """Build synthetic/calculated gage readings from expressions."""

    session = get_session()
    try:
        # Find sources with calculation expressions
        calc_sources = session.query(Source).filter(Source.calc_expression_id.isnot(None)).all()

        print(f"Found {len(calc_sources)} calculated sources")

        # Build gauge name -> gauge_id lookup
        name_to_gauge_id = {g.name: g.id for g in session.query(Gauge).all()}

        # Build source_id -> gauge_id and gauge_id -> gauge_name reverse lookups
        source_to_gauge: dict[int, int] = {}
        for gs in session.query(GaugeSource).all():
            source_to_gauge[gs.source_id] = gs.gauge_id
        gauge_id_to_name: dict[int, str] = {gid: name for name, gid in name_to_gauge_id.items()}

        # Topological sort: calc sources that depend on other calcs run last
        calc_gauge_names = {
            gauge_id_to_name.get(source_to_gauge.get(s.id, -1), ""): s for s in calc_sources
        }

        def _get_deps(source: Source) -> list[str]:
            ce = source.calc_expression
            if not ce or not ce.time_expression:
                return []
            return [
                ref_name
                for _, ref_name, _ in re.findall(r"(\w+)::(\w+)::(\w+)", ce.time_expression)
                if ref_name in calc_gauge_names
            ]

        # Simple topo sort: sources with no calc deps first, then dependents
        sorted_sources: list[Source] = []
        remaining = list(calc_sources)
        resolved: set[str] = set()
        max_iterations = len(remaining) + 1
        while remaining and max_iterations > 0:
            max_iterations -= 1
            progress = False
            next_remaining = []
            for source in remaining:
                deps = _get_deps(source)
                if all(d in resolved for d in deps):
                    sorted_sources.append(source)
                    src_gauge_name = gauge_id_to_name.get(source_to_gauge.get(source.id, -1), "")
                    if src_gauge_name:
                        resolved.add(src_gauge_name)
                    progress = True
                else:
                    next_remaining.append(source)
            remaining = next_remaining
            if not progress:
                circular_names = [
                    gauge_id_to_name.get(source_to_gauge.get(s.id, -1), s.name) for s in remaining
                ]
                logger.warning(
                    "Circular dependency detected among calculated sources: %s. "
                    "Evaluating in arbitrary order.",
                    ", ".join(circular_names),
                )
                sorted_sources.extend(remaining)
                break
        calc_sources = sorted_sources

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
                    source.name,
                    data_type.value,
                    expression,
                )

                if not time_expression:
                    logger.warning("No time_expression for source %s", source.name)
                    continue

                # Resolve all references to gauge-level latest values
                # time_expression refs are "key::gauge_name::type" (3-part)
                # or "gauge_name::type" (2-part)
                values: dict[str, float] = {}
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

                    ref_gauge_id = name_to_gauge_id.get(ref_name)
                    if not ref_gauge_id:
                        logger.error("No gauge for name %s", ref_name)
                        skip = True
                        break

                    try:
                        ref_dtype = DataType(ref_type_str)
                    except ValueError:
                        logger.error("Unknown type: %s", ref_type_str)
                        skip = True
                        break

                    latest = get_latest_gauge(session, ref_gauge_id, ref_dtype)
                    if latest is None or latest.value is None:
                        logger.warning("No latest gauge value for %s/%s", ref_name, ref_type_str)
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
                    # Also update gauge-level cache
                    gauge_id = source_to_gauge.get(source.id)
                    if gauge_id:
                        update_latest_gauge(session, gauge_id, data_type)
                    logger.debug("  = %.1f at %s", result, when)

                # Commit after each source to release the write lock
                session.commit()

            except Exception as e:
                session.rollback()
                logger.error("Error for %s: %s", source.name, e)

        print("Calculations complete")
    finally:
        session.close()
