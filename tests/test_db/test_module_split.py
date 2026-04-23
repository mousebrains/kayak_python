"""T3-20 drift guard.

After the data_db / info_db → observations/cache/gauges/reaches/sources
split, the old modules remain as re-export shims. This test asserts that
every name the shims advertise is callable and points at the same object
as its new home — so we catch accidental drift (e.g. a rename in the new
module that isn't mirrored in the shim).

The page_db shim + kayak.db.pages module were removed alongside migration
0006 (drop pages table); no drift guard here.
"""

from __future__ import annotations


def test_data_db_shim_reexports_match():
    """Every name in data_db.__all__ resolves to the canonical callable."""
    from kayak.db import cache, data_db, gauges, observations, sources

    canonical = {
        # cache
        "DELTA_LOOKBACK_WINDOW": cache.DELTA_LOOKBACK_WINDOW,
        "update_latest": cache.update_latest,
        "get_latest": cache.get_latest,
        "get_all_latest": cache.get_all_latest,
        "update_latest_gauge": cache.update_latest_gauge,
        "update_all_latest_gauges": cache.update_all_latest_gauges,
        "get_latest_gauge": cache.get_latest_gauge,
        "get_all_latest_gauges": cache.get_all_latest_gauges,
        # gauges
        "get_gauge_by_name": gauges.get_gauge_by_name,
        "get_bulk_gauge_observations": gauges.get_bulk_gauge_observations,
        # observations
        "store_observation": observations.store_observation,
        "store_observations": observations.store_observations,
        "get_observations": observations.get_observations,
        "get_bulk_observations": observations.get_bulk_observations,
        "get_rating_table": observations.get_rating_table,
        "put_rating_table": observations.put_rating_table,
        "merge_sources": observations.merge_sources,
        # sources
        "get_source_by_name": sources.get_source_by_name,
        "get_negative_flow_source_ids": sources.get_negative_flow_source_ids,
    }

    for name, obj in canonical.items():
        assert getattr(data_db, name) is obj, f"data_db.{name} does not match canonical"
    # Every exported name should also appear in __all__.
    assert set(data_db.__all__) == set(canonical), "data_db.__all__ drifted from canonical set"


def test_info_db_shim_reexports_match():
    from kayak.db import gauges, info_db, reaches, sources

    canonical = {
        "all_states": reaches.all_states,
        "all_state_names": reaches.all_state_names,
        "reaches_query": reaches.reaches_query,
        "get_reach": reaches.get_reach,
        "get_reach_by_name": reaches.get_reach_by_name,
        "display_name": reaches.display_name,
        "get_gauge_for_reach": reaches.get_gauge_for_reach,
        "classify_level": reaches.classify_level,
        "get_primary_source_id": gauges.get_primary_source_id,
        "get_source_ids_for_gauge": gauges.get_source_ids_for_gauge,
        "get_all_primary_source_ids": gauges.get_all_primary_source_ids,
        "get_calculated_gauge_ids": gauges.get_calculated_gauge_ids,
        "is_source_calculated": sources.is_source_calculated,
        "get_calculated_source_ids": sources.get_calculated_source_ids,
    }

    for name, obj in canonical.items():
        assert getattr(info_db, name) is obj, f"info_db.{name} does not match canonical"
    assert set(info_db.__all__) == set(canonical), "info_db.__all__ drifted from canonical set"
