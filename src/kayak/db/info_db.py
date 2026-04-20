"""Deprecated facade — kept for back-compat while callers migrate.

Every function below has moved to a narrower module:

- reaches: all_states, all_state_names, reaches_query, get_reach,
  get_reach_by_name, display_name, get_gauge_for_reach, classify_level
- gauges: get_primary_source_id, get_source_ids_for_gauge,
  get_all_primary_source_ids, get_calculated_gauge_ids
- sources: is_source_calculated, get_calculated_source_ids

New code should import from those modules directly.
"""

from kayak.db.gauges import (
    get_all_primary_source_ids,
    get_calculated_gauge_ids,
    get_primary_source_id,
    get_source_ids_for_gauge,
)
from kayak.db.reaches import (
    all_state_names,
    all_states,
    classify_level,
    display_name,
    get_gauge_for_reach,
    get_reach,
    get_reach_by_name,
    reaches_query,
)
from kayak.db.sources import get_calculated_source_ids, is_source_calculated

__all__ = [
    "all_state_names",
    "all_states",
    "classify_level",
    "display_name",
    "get_all_primary_source_ids",
    "get_calculated_gauge_ids",
    "get_calculated_source_ids",
    "get_gauge_for_reach",
    "get_primary_source_id",
    "get_reach",
    "get_reach_by_name",
    "get_source_ids_for_gauge",
    "is_source_calculated",
    "reaches_query",
]
