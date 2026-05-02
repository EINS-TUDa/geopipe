"""Core contracts and shared graph conversion primitives.

Only owns topology build interfaces and basic GeoDataFrame-to-graph conversion.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import geopandas as gpd
import networkx as nx


@dataclass
class TopologyBuildResult:
    network: nx.Graph
    region_topologies: dict[str, nx.Graph]
    streets: gpd.GeoDataFrame


class TopologyBuildError(RuntimeError):
    """Raised when topology building fails."""


def gdf_to_nx(gdf: gpd.GeoDataFrame) -> nx.Graph:
    """Convert line-segment GeoDataFrame rows into a street graph."""
    graph: nx.Graph = nx.Graph()
    if hasattr(gdf, "crs") and gdf.crs is not None:
        graph.graph["crs"] = gdf.crs

    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue

        attrs = {col: row[col] for col in gdf.columns if col != "geometry"}

        if geom.geom_type == "LineString":
            lines = [geom]
        elif geom.geom_type == "MultiLineString":
            lines = list(geom.geoms)
        else:
            continue

        for line in lines:
            coords = list(line.coords)
            for idx in range(len(coords) - 1):
                u = (float(coords[idx][0]), float(coords[idx][1]))
                v = (float(coords[idx + 1][0]), float(coords[idx + 1][1]))
                length = ((v[0] - u[0]) ** 2 + (v[1] - u[1]) ** 2) ** 0.5
                graph.add_edge(u, v, geometry=line, length=length, **attrs)

    return graph


def gdf_to_region_topologies(
    gdf: gpd.GeoDataFrame,
    *,
    key_column: str,
) -> list[nx.Graph]:
    """Group streets by key and convert each group into a region graph."""
    topologies: list[nx.Graph] = []
    for key, group in gdf.groupby(key_column, sort=True):
        region_graph = gdf_to_nx(group.copy())
        region_graph.graph["id"] = key
        topologies.append(region_graph)
    return topologies


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

    assigned_edges = int(gdf_to_nx(assigned).number_of_edges())
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


class AbstractTopologyBuilder(ABC):
    """Interface for all graph-first topology builders."""

    @abstractmethod
    def build(self) -> TopologyBuildResult:
        raise NotImplementedError

    @staticmethod
    def gdf_to_nx(gdf: gpd.GeoDataFrame) -> nx.Graph:
        """Compatibility wrapper for module-level `gdf_to_nx`."""
        return gdf_to_nx(gdf)

    @staticmethod
    def gdf_to_region_topologies(
        gdf: gpd.GeoDataFrame,
        *,
        key_column: str,
    ) -> list[nx.Graph]:
        """Compatibility wrapper for module-level `gdf_to_region_topologies`."""
        return gdf_to_region_topologies(gdf, key_column=key_column)


__all__ = [
    "TopologyBuildResult",
    "TopologyBuildError",
    "gdf_to_nx",
    "gdf_to_region_topologies",
    "edge_metrics_from_topology_result",
    "streets_for_topology_plot",
    "AbstractTopologyBuilder",
]
