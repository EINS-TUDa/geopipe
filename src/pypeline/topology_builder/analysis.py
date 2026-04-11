from __future__ import annotations

from typing import Any

import geopandas as gpd

from pypeline.topology_builder.abstract_topology_builder import AbstractTopologyBuilder


def edge_metrics_from_topology_result(
    topology_result: Any,
    *,
    region_id_column: str = "id",
) -> tuple[int, int]:
    if not hasattr(topology_result, "network"):
        raise ValueError("topology_result is missing network")

    streets = getattr(topology_result, "streets", None)
    if streets is None:
        raise ValueError("topology_result is missing streets")
    if region_id_column not in streets.columns:
        raise ValueError(f"streets are missing region id column '{region_id_column}'")

    total_edges = int(topology_result.network.number_of_edges())
    assigned = streets[streets[region_id_column].notna()].copy()
    if assigned.empty:
        raise ValueError("topology_result has no assigned streets")

    assigned_edges = int(AbstractTopologyBuilder.gdf_to_nx(assigned).number_of_edges())
    return total_edges, assigned_edges


def streets_for_topology_plot(
    topology_result: Any,
    *,
    region_id_column: str = "id",
    min_context_buffer_m: float = 750.0,
    context_span_factor: float = 2.0,
) -> gpd.GeoDataFrame:
    streets = getattr(topology_result, "streets", None)
    if streets is None:
        raise ValueError("topology_result is missing streets")
    if region_id_column not in streets.columns:
        raise ValueError(f"streets are missing region id column '{region_id_column}'")

    assigned = streets[streets[region_id_column].notna()].copy()
    if assigned.empty:
        raise ValueError("topology_result has no assigned streets")

    minx, miny, maxx, maxy = assigned.total_bounds
    span = max(float(maxx - minx), float(maxy - miny), 1.0)
    buffer_m = max(float(min_context_buffer_m), span * float(context_span_factor))

    cminx = float(minx) - buffer_m
    cminy = float(miny) - buffer_m
    cmaxx = float(maxx) + buffer_m
    cmaxy = float(maxy) + buffer_m

    bounds = streets.geometry.bounds
    local_mask = (
        (bounds["maxx"] >= cminx)
        & (bounds["minx"] <= cmaxx)
        & (bounds["maxy"] >= cminy)
        & (bounds["miny"] <= cmaxy)
    )
    local = streets[local_mask].copy()
    if local.empty:
        return assigned
    return local
