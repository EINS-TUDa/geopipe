# coding=utf-8

from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Self, Any
import logging

import geopandas as gpd
import networkx as nx
import shapely
from networkx.algorithms.components import is_connected

from .topology_build_utils import gdf_to_nx, load_yaml, TopologyBuildResult

logger = logging.getLogger(__name__)


class TopologyBuilder(ABC):

    def __init__(self):
        self._streets_data = None
        self._region_id_column = "region"
        self._default_region = None
        self._street_network = None
        self._topologies = None
        self._default_regions_in_topology: bool = False
        self._extensive_columns: list[str] = []

    def set_streets_data(self, streets_data: gpd.GeoDataFrame | Path) -> Self:
        if isinstance(streets_data, Path):
            streets_data = gpd.read_file(streets_data)
        self._streets_data = streets_data
        return self

    def set_region_id_column(self, region_id_column: str) -> Self:
        self._region_id_column = region_id_column
        return self

    def set_default_region(self, default_region) -> Self:
        self._default_region = default_region
        return self

    def set_default_regions_in_topology(self, default_regions_in_topology: bool) -> Self:
        self._default_regions_in_topology = default_regions_in_topology

    def set_extensive_columns(self, extensive_columns: list[str]) -> Self:
        """Columns whose values are length-additive (e.g. demand totals) and must be
        split across segments by length share when a row is broken into multiple edges.
        """
        self._extensive_columns = list(extensive_columns)
        return self

    def _check_input(self):
        if not isinstance(self._streets_data, gpd.GeoDataFrame):
            raise TypeError("Streets data must be a GeoDataFrame")

        if self._region_id_column in self._streets_data:
            logger.warning("Column for regions already exists (name %s). The region data will be overwritten",
                           self._region_id_column)
        if not self._extensive_columns:
            logger.warning("No extensive columns specified. Demands have to be set as extensive.")

    def _setup(self):
        self._streets_data[self._region_id_column] = self._default_region

    @abstractmethod
    def _define_regions(self) -> None:
        ...

    def _build_street_network(self) -> nx.Graph:
        self._street_network = gdf_to_nx(self._streets_data, extensive_columns=self._extensive_columns)

    def _build_topologies(self):
        topologies = defaultdict(nx.Graph)
        for u, v, data in self._street_network.edges(data=True):
            if not self._default_regions_in_topology and data.get(self._region_id_column) == self._default_region:
                continue
            topologies[data.get(self._region_id_column)].add_edge(u, v, **data)
        self._topologies = dict(topologies)

    def _topologies_check(self):
        for region_id, region_topology in self._topologies.items():
            if not is_connected(region_topology):
                raise ValueError(f"Topology of region {region_id} is not connected")

    def build(self) -> TopologyBuildResult:
        """Build the topology"""
        self._check_input()

        self._setup()
        self._define_regions()

        self._build_street_network()
        self._build_topologies()

        self._topologies_check()
        return TopologyBuildResult(self._street_network, self._topologies, self._streets_data)


class PolygonTopologyBuilder(TopologyBuilder):

    def __init__(self):
        super().__init__()
        self._polygons_data = None
        self._streets_geometry_column_name = ["geometry", "geom"]
        self._polygons_geometry_column_name = ["geometry", "geom"]
        # Todo: Add option to not check if each street segment is in any polygon

    def set_polygons_data(self, polygons_data: gpd.GeoDataFrame | Path) -> Self:
        if isinstance(polygons_data, Path):
            polygons_data = gpd.read_file(polygons_data)
        self._polygons_data = polygons_data
        return self

    def set_streets_geometry_column_name(self, street_geometry_column_name: str) -> Self:
        self._streets_geometry_column_name = street_geometry_column_name
        return self

    def set_polygons_geometry_column_name(self, polygon_geometry_column_name: str) -> Self:
        self._polygons_geometry_column_name = polygon_geometry_column_name
        return self

    def _check_input(self):
        super()._check_input()
        if self._polygons_data is None:
            raise ValueError("Polygons data must be set")

    def _setup(self):
        super()._setup()
        if isinstance(self._streets_geometry_column_name, list):
            for streets_geometry_column_name in self._streets_geometry_column_name:
                if streets_geometry_column_name in self._streets_data:
                    self._streets_geometry_column_name = streets_geometry_column_name
                    logger.info("Using the name '%s' for the streets_geometry_column_name",
                                streets_geometry_column_name)
                    break
            else:
                raise ValueError("'street_geometry_column_name' does not exist")

        if isinstance(self._polygons_geometry_column_name, list):
            for polygons_geometry_column_name in self._polygons_geometry_column_name:
                if polygons_geometry_column_name in self._streets_data:
                    self._polygons_geometry_column_name = polygons_geometry_column_name
                    logger.info("Using the name '%s' for the polygons_geometry_column_name",
                                polygons_geometry_column_name)
                    break
            else:
                raise ValueError("'polygons_geometry_column_name' does not exist")

    def _define_regions(self) -> None:
        for street_index, street_geometry in zip(self._streets_data.index,
                                                 self._streets_data[self._streets_geometry_column_name]):
            street_geometry: shapely.MultiLineString
            for region_name, region_geometry in zip(self._polygons_data[self._region_id_column],
                                                    self._polygons_data[self._polygons_geometry_column_name]):
                region_geometry: shapely.MultiPolygon
                if not region_geometry.contains(street_geometry):
                    continue
                if self._streets_data.at[street_index, self._region_id_column] != self._default_region:
                    logger.error("Street at index %d lies within multiple polygons with id: %d and %d",
                                 street_index, self._streets_data.at[street_index, self._region_id_column], region_name)
                    raise ValueError("Street lies within multiple polygons")
                self._streets_data.at[street_index, self._region_id_column] = region_name


class SimpleTopologyBuilder(TopologyBuilder):

    def __init__(self):
        super().__init__()
        self._grouping = None

    def set_grouping(self, grouping: dict[str, dict[str, list[Any]]] | Path) -> Self:
        if isinstance(grouping, Path):
            grouping = load_yaml(grouping)
        self._grouping = grouping

    def _check_input(self):
        super()._check_input()
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

    def _define_regions(self) -> None:
        for region_name, data in self._grouping.items():
            for column_name, values in data.items():
                if column_name == "index":
                    self._streets_data.loc[self._streets_data.index.isin(values), self._region_id_column] = region_name
                else:
                    self._streets_data.loc[self._streets_data[column_name].isin(values), self._region_id_column] = region_name
