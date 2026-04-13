"""Public builder entrypoint for region topology construction.

Only owns API-facing region topology. Implementation lives in core and engine.
"""

from __future__ import annotations
from typing import Any
import geopandas as gpd
import pandas as pd

from pypeline.topology_builder.region_topology_core import (
    REGION_TOPOLOGY_DEFAULTS,
    RegionCaps,
    RegionTopologyBase,
    RegionTopologyConfig,
    RegionTopologyDefaults,
    RegionTopologyGeometry,
    _build_region_topology_impl,
    _region_seed_builder_impl,
)


def region_seed_builder(*args: Any, **kwargs: Any) -> pd.DataFrame:
    return _region_seed_builder_impl(*args, **kwargs)


def build_region_topology(
    *,
    buildings: gpd.GeoDataFrame,
    streets: gpd.GeoDataFrame,
    demand_data: pd.DataFrame | gpd.GeoDataFrame,
    config: RegionTopologyConfig,
) -> tuple[gpd.GeoDataFrame, dict[str, int]]:
    return _build_region_topology_impl(
        buildings=buildings,
        streets=streets,
        demand_data=demand_data,
        config=config,
    )

__all__ = [
    "RegionCaps",
    "RegionTopologyConfig",
    "RegionTopologyDefaults",
    "REGION_TOPOLOGY_DEFAULTS",
    "RegionTopologyBase",
    "RegionTopologyGeometry",
    "region_seed_builder",
    "build_region_topology",
]
