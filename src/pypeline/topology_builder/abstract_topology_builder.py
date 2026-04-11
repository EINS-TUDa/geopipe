from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import networkx as nx


@dataclass
class TopologyBuildResult:

    network: nx.Graph
    region_topologies: list[nx.Graph]
    streets: gpd.GeoDataFrame
    injected_demand_mwh: float = 0.0
    injected_techs: list[dict[str, Any]] = field(default_factory=list)


class TopologyBuildError(RuntimeError):
    """Raised when topology building fails."""


class AbstractTopologyBuilder(ABC):
    """Interface for all graph-first topology builders."""

    @abstractmethod
    def build(self) -> TopologyBuildResult:
        raise NotImplementedError

    @staticmethod
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

    @staticmethod
    def gdf_to_region_topologies(
        gdf: gpd.GeoDataFrame,
        *,
        key_column: str,
    ) -> list[nx.Graph]:
        """Group streets by key and convert each group into a region graph."""
        topologies: list[nx.Graph] = []
        for key, group in gdf.groupby(key_column, sort=True):
            region_graph = AbstractTopologyBuilder.gdf_to_nx(group.copy())
            region_graph.graph["id"] = key
            topologies.append(region_graph)
        return topologies
