"""Core contracts and shared graph conversion primitives.

Only owns topology build interfaces and basic GeoDataFrame-to-graph conversion.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import yaml
from typing import Any
import geopandas as gpd
import networkx as nx


def modify_streets_data(streets_data: gpd.GeoDataFrame | Path,
                        modifications_file: Path, ) -> gpd.GeoDataFrame:
    """Modify the data of a geopandas GeoDataFrame according to the specifications in a YAML file.

    The yaml file mus have the following structure. The elements in the GeoDataFrame is described with a column name
    and a row specifier which can be either the row index or the entry in another column.
    For each entry, there are two actions supported: add and replace.

    .. code-block:: yaml
        column_name:  # Name of the column where to change
          - index: 0  # Index of the row where to change
            add: 50
          - column_name: value_in_column  # Specifier of the row where to change, e.g. column_name: value_in_column
            replace: 100

    Parameters
    ----------
    streets_data : gpd.GeoDataFrame | Path
        A GeoDataFrame containing the street data or a path to a file that can be read into a GeoDataFrame.
    modifications_file : Path
        A path to a YAML file that specifies the modifications to be made to the GeoDataFrame.

    Returns
    -------
    modified_streets_data : gpd.GeoDataFrame
    """
    if isinstance(streets_data, Path):
        streets_data = gpd.read_file(streets_data)
    streets_data: gpd.GeoDataFrame

    modifications_data = load_yaml(modifications_file)

    for column_name, mod_data_per_street in modifications_data.items():
        column_name: str
        mod_data_per_street: list[dict[str, Any]]
        for mod_data in mod_data_per_street:
            mod_data: dict[str, Any]
            if len(mod_data) != 2:
                raise ValueError()

            add_value = mod_data.pop("add", None)
            replace_value = mod_data.pop("replace", None)

            if add_value is None and replace_value is None:
                raise ValueError()
            if add_value is not None and replace_value is not None:
                raise ValueError()

            [(key, value)] = mod_data.items()
            if key == "index":
                row_specifier = streets_data.index == value
            else:
                row_specifier = streets_data[key] == value

            value = streets_data.loc[row_specifier, column_name].iloc[0]

            if add_value is not None:
                value = value + add_value
            if replace_value is not None:
                value = replace_value

            streets_data.loc[row_specifier, column_name] = value

    return streets_data


def load_yaml(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        raise FileNotFoundError(f"Scenario file not found: {config_file}")
    with config_file.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping root in {config_file}, got {type(data).__name__}")
    return data


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
