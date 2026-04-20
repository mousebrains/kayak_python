"""Deprecated facade — kept for back-compat while callers migrate.

Every function below has moved to a narrower module:

- observations: store_observation, store_observations, get_observations,
  get_bulk_observations, get_rating_table, put_rating_table, merge_sources
- cache: update_latest, get_latest, get_all_latest, update_latest_gauge,
  update_all_latest_gauges, get_latest_gauge, get_all_latest_gauges
- gauges: get_gauge_by_name, get_bulk_gauge_observations
- sources: get_source_by_name, get_negative_flow_source_ids

New code should import from those modules directly.
"""

from kayak.db.cache import (
    DELTA_LOOKBACK_WINDOW,
    get_all_latest,
    get_all_latest_gauges,
    get_latest,
    get_latest_gauge,
    update_all_latest_gauges,
    update_latest,
    update_latest_gauge,
)
from kayak.db.gauges import get_bulk_gauge_observations, get_gauge_by_name
from kayak.db.observations import (
    get_bulk_observations,
    get_observations,
    get_rating_table,
    merge_sources,
    put_rating_table,
    store_observation,
    store_observations,
)
from kayak.db.sources import get_negative_flow_source_ids, get_source_by_name

__all__ = [
    "DELTA_LOOKBACK_WINDOW",
    "get_all_latest",
    "get_all_latest_gauges",
    "get_bulk_gauge_observations",
    "get_bulk_observations",
    "get_gauge_by_name",
    "get_latest",
    "get_latest_gauge",
    "get_negative_flow_source_ids",
    "get_observations",
    "get_rating_table",
    "get_source_by_name",
    "merge_sources",
    "put_rating_table",
    "store_observation",
    "store_observations",
    "update_all_latest_gauges",
    "update_latest",
    "update_latest_gauge",
]
