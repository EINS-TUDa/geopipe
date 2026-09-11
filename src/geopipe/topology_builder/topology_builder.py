# coding=utf-8

from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Self, Any, Optional, TYPE_CHECKING
import logging

import geopandas as gpd
import networkx as nx
import pandas as pd
from networkx.algorithms.components import is_connected

from .topology import REGION_ID, SOURCE_STREET_ID, SOURCE_SHARE
from .topology_build_utils import gdf_to_nx, load_yaml, TopologyBuildResult

if TYPE_CHECKING:
    from geopipe.data.data_registry import DataRegistry

logger = logging.getLogger(__name__)


def _no_regions(streets: gpd.GeoDataFrame) -> pd.Series:
    """A region id per street, all None (no region)."""
    return pd.Series([None] * len(streets), index=streets.index, dtype=object)


class TopologyBuilder(ABC):
    """Builds the street network from the streets of the data registry and splits it into region topologies.

    Subclasses define the area whose streets are queried and how streets are assigned to regions.
    """

    def __init__(self):
        self._data_registry: Optional["DataRegistry"] = None
        self._check_topology_connections: bool = True

    def set_data_registry(self, data_registry: "DataRegistry") -> Self:
        """Set the data registry. The streets are queried from it; its CRS is the project CRS."""
        self._data_registry = data_registry
        return self

    def set_topology_connections_check(self, enabled: bool) -> Self:
        """Whether to check that each region's topology is fully connected, and raise an
        error if not. Enabled by default.
        """
        self._check_topology_connections = enabled
        return self

    def _check_input(self):
        if self._data_registry is None:
            raise ValueError("Data registry must be set via set_data_registry")

    @abstractmethod
    def _area(self) -> gpd.GeoDataFrame:
        """The area whose streets are queried from the data registry."""

    @abstractmethod
    def _assign_regions(self, streets: gpd.GeoDataFrame) -> pd.Series:
        """Region id per street; None for streets outside all regions."""

    @staticmethod
    def _build_topologies(network: nx.Graph) -> dict[Any, nx.Graph]:
        topologies = defaultdict(nx.Graph)
        for u, v, data in network.edges(data=True):
            if pd.isna(data.get(REGION_ID)):
                continue
            topologies[data[REGION_ID]].add_edge(u, v, **data)
        return dict(topologies)

    @staticmethod
    def _topologies_connections_check(topologies: dict[Any, nx.Graph]):
        errors = []
        for region_id, region_topology in topologies.items():
            if not is_connected(region_topology):
                components = sorted(
                    nx.connected_components(region_topology), key=len, reverse=True
                )
                isolated_street_ids = []
                for component in components[1:]:
                    subgraph = region_topology.subgraph(component)
                    for _, _, data in subgraph.edges(data=True):
                        street_id = data.get(SOURCE_STREET_ID)
                        if street_id is not None and street_id not in isolated_street_ids:
                            isolated_street_ids.append(street_id)
                msg = (
                    f"Topology of region {region_id} is not connected. "
                    f"Found {len(components)} connected segment groups (largest has {len(components[0])} nodes). "
                    f"Street IDs not connected to the main segment group: {isolated_street_ids}"
                )
                logger.error(msg)
                errors.append(msg)

        if errors:
            raise ValueError("\n".join(errors))

    def build(self) -> TopologyBuildResult:
        """Build the topology"""
        self._check_input()
        streets = self._data_registry.streets(self._area())
        if REGION_ID in streets:
            logger.warning("Column '%s' already exists in the streets data. It will be overwritten", REGION_ID)
        streets = streets.assign(**{REGION_ID: self._assign_regions(streets)})

        network = gdf_to_nx(streets, extensive_columns=[SOURCE_SHARE])
        topologies = self._build_topologies(network)

        if self._check_topology_connections:
            self._topologies_connections_check(topologies)
        return TopologyBuildResult(network, topologies, streets)


class PolygonTopologyBuilder(TopologyBuilder):
    """Regions from a polygon layer: a street lying completely within a polygon belongs to its region."""

    def __init__(self):
        super().__init__()
        self._polygons_data: Optional[gpd.GeoDataFrame] = None
        self._polygons_id_column: Optional[str] = None

    def set_polygons_data(self, polygons_data: gpd.GeoDataFrame | Path, id_column: str) -> Self:
        """Set the polygons defining the regions; ``id_column`` holds the region id. The polygons are also the
        area whose streets are queried."""
        if isinstance(polygons_data, Path):
            polygons_data = gpd.read_file(polygons_data)
        self._polygons_data = polygons_data
        self._polygons_id_column = id_column
        return self

    def _check_input(self):
        super()._check_input()
        if self._polygons_data is None:
            raise ValueError("Polygons data must be set via set_polygons_data")
        if self._polygons_data.crs is None:
            raise ValueError("Polygons data has no CRS")
        if self._polygons_id_column not in self._polygons_data:
            raise ValueError(f"Polygons id column '{self._polygons_id_column}' does not exist in the polygons data")

    def _polygons(self) -> gpd.GeoDataFrame:
        if self._polygons_data.crs != self._data_registry.crs:
            self._polygons_data = self._polygons_data.to_crs(self._data_registry.crs)
        return self._polygons_data

    def _area(self) -> gpd.GeoDataFrame:
        return self._polygons()

    def _assign_regions(self, streets: gpd.GeoDataFrame) -> pd.Series:
        polygons = self._polygons()
        regions = _no_regions(streets)
        violating_streets = []
        for street_index, street_geometry in zip(streets.index, streets.geometry):
            for region_id, region_geometry in zip(polygons[self._polygons_id_column], polygons.geometry):
                if not region_geometry.contains(street_geometry):
                    continue
                existing_region = regions.at[street_index]
                if not pd.isna(existing_region):
                    street_id = streets.at[street_index, SOURCE_STREET_ID]
                    logger.error("Street %s lies within multiple polygons with id: %s and %s",
                                 street_id, existing_region, region_id)
                    violating_streets.append((street_id, existing_region, region_id))
                    continue
                regions.at[street_index] = region_id

        if violating_streets:
            details = ", ".join(
                f"street {street_id} (polygons {existing_region} and {region_id})"
                for street_id, existing_region, region_id in violating_streets
            )
            raise ValueError(f"{len(violating_streets)} street(s) lie within multiple polygons: {details}")
        return regions


class SimpleTopologyBuilder(TopologyBuilder):
    """Regions listed explicitly: street column values or row indices per region."""

    def __init__(self):
        super().__init__()
        self._grouping = None
        self._area_data: Optional[gpd.GeoDataFrame] = None

    def set_grouping(self, grouping: dict[str, dict[str, list[Any]]] | Path) -> Self:
        if isinstance(grouping, Path):
            grouping = load_yaml(grouping)
        self._grouping = grouping
        return self

    def set_area(self, area: gpd.GeoDataFrame | Path) -> Self:
        """Set the study area whose streets are queried from the data registry."""
        if isinstance(area, Path):
            area = gpd.read_file(area)
        self._area_data = area
        return self

    def _check_input(self):
        super()._check_input()
        if self._area_data is None:
            raise ValueError("Area must be set via set_area")
        if self._area_data.crs is None:
            raise ValueError("Area has no CRS")
        if self._grouping is None:
            raise ValueError("Grouping must be set")

        if not isinstance(self._grouping, dict):
            raise TypeError("Grouping must be a dict")

        for key, value in self._grouping.items():
            if not isinstance(key, int):
                raise TypeError("Key must be an integer")
            if not isinstance(value, dict):
                raise TypeError("Value must be a dict")

            for key_inner, value_inner in value.items():
                if not isinstance(key_inner, str):
                    raise TypeError("Key must be a string")
                if not isinstance(value_inner, list):
                    raise TypeError("Value must be a list")

    def _area(self) -> gpd.GeoDataFrame:
        return self._area_data

    def _assign_regions(self, streets: gpd.GeoDataFrame) -> pd.Series:
        regions = _no_regions(streets)
        for region_id, data in self._grouping.items():
            for column_name, values in data.items():
                if column_name == "index":
                    regions[streets.index.isin(values)] = region_id
                else:
                    regions[streets[column_name].isin(values)] = region_id
        return regions
