# coding=utf-8

from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Self, Any, Optional, TYPE_CHECKING
import logging

import geopandas as gpd
import networkx as nx
import shapely
from networkx.algorithms.components import is_connected

from .topology_build_utils import (gdf_to_nx, load_yaml, TopologyBuildResult,
                                    close_dead_end_gaps, drop_null_isolated_segments,
                                    divide_segments_at_junctions)

if TYPE_CHECKING:
    from geopipe.data.data_registry import DataRegistry

logger = logging.getLogger(__name__)


class TopologyBuilder(ABC):

    def __init__(self):
        self._streets_data: gpd.GeoDataFrame = None
        self._region_id_column = "region"
        self._default_region = None
        self._street_network = None
        self._topologies = None
        self._default_regions_in_topology: bool = False
        self._extensive_columns: list[str] = []
        self._gap_distance: float | None = None
        self._drop_isolated_null: bool = False
        self._streets_id_column: str | None = None
        self._divide_at_junctions: bool = False
        self._junction_tol: float = 1e-6
        self._check_topology_connections: bool = True
        self._data_registry: Optional["DataRegistry"] = None

    def set_data_registry(self, data_registry: "DataRegistry") -> Self:
        """Set the data registry. Its CRS is the project CRS: all input geometries are reprojected to it."""
        self._data_registry = data_registry
        return self

    def set_streets_data(self, streets_data: gpd.GeoDataFrame | Path,
                         id_column: str) -> Self:
        """Set the streets data.

        ``id_column`` is a column holding a stable identifier for each street (e.g. the
        source feature id).
        """
        if isinstance(streets_data, Path):
            streets_data = gpd.read_file(streets_data)
        self._streets_data = streets_data
        self._streets_id_column = id_column
        return self

    def set_region_id_column(self, region_id_column: str) -> Self:
        self._region_id_column = region_id_column
        return self

    def set_default_region(self, default_region) -> Self:
        self._default_region = default_region
        return self

    def set_default_regions_in_topology(self, default_regions_in_topology: bool) -> Self:
        self._default_regions_in_topology = default_regions_in_topology
        return self

    def set_extensive_columns(self, extensive_columns: list[str]) -> Self:
        """Columns whose values are length-additive (e.g. demand totals) and must be
        split across segments by length share when a row is broken into multiple edges.
        """
        self._extensive_columns = list(extensive_columns)
        return self

    def set_gap_distance(self, gap_distance: float | None) -> Self:
        """Maximum distance (in CRS units) to bridge between a dead-end and the nearest
        unconnected street. ``None`` disables gap closing.
        """
        self._gap_distance = gap_distance
        return self

    def set_drop_isolated_null_segments(self, drop_isolated_null: bool) -> Self:
        """Drop still-isolated segments whose extensive columns are all NULL (no demand)
        during cleaning. Runs after gap closing.
        """
        self._drop_isolated_null = drop_isolated_null
        return self

    def set_divide_at_junctions(self, enabled: bool, tol: float = 1e-6) -> Self:
        """Split segments at junctions and true mid-segment crossings.

        Disabled by default. A street touching another at a T-junction, or crossing it
        mid-segment, is geometrically connected but graph-disconnected; this cuts the
        crossed street at the meeting point so the streets share a graph node. Each
        resulting piece becomes its own row with a distinct id (``100<id>``, ``200<id>``,
        ...) and its extensive columns split by length share. ``tol`` (CRS units) is the
        max distance for a point to count as lying on a segment.
        """
        self._divide_at_junctions = enabled
        self._junction_tol = tol
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
        if not isinstance(self._streets_data, gpd.GeoDataFrame):
            raise TypeError("Streets data must be a GeoDataFrame")
        if self._streets_data.crs is None:
            raise ValueError("Streets data has no CRS")

        if self._streets_id_column is None:
            raise ValueError("Streets id column must be set via set_streets_data")
        if self._streets_id_column not in self._streets_data:
            raise ValueError(f"Streets id column '{self._streets_id_column}' does not exist in the streets data")

        if self._region_id_column in self._streets_data:
            logger.warning("Column for regions already exists (name %s). The region data will be overwritten",
                           self._region_id_column)
        if not self._extensive_columns:
            raise ValueError("No extensive columns specified. Demands have to be set as extensive.")

    def _to_project_crs(self) -> None:
        """Reproject the input geometries to the project CRS of the data registry."""
        if self._streets_data.crs != self._data_registry.crs:
            self._streets_data = self._streets_data.to_crs(self._data_registry.crs)

    def _setup(self):
        self._streets_data[self._region_id_column] = self._default_region

    def _clean_streets_geometry(self) -> None:
        """Divide segments at junctions, close dead-end gaps and drop demand-less
        isolated segments.

        Junction division (:meth:`set_divide_at_junctions`, off by default) runs first
        when enabled, so a street touching another at a T-/X-junction shares a graph node,
        and so gap closing does not mistake such an already-connected endpoint for a dead-end.
        Gap closing and isolated-segment dropping are opt-in (:meth:`set_gap_distance` and
        :meth:`set_drop_isolated_null_segments`). Runs before region assignment so regions
        are derived from the cleaned geometry.
        """
        if self._divide_at_junctions:
            self._streets_data = divide_segments_at_junctions(
                self._streets_data, self._extensive_columns, self._junction_tol,
                id_column=self._streets_id_column)

        if self._gap_distance is not None:
            self._streets_data = close_dead_end_gaps(
                self._streets_data, self._gap_distance, self._extensive_columns,
                id_column=self._streets_id_column)
        if self._drop_isolated_null:
            self._streets_data = drop_null_isolated_segments(
                self._streets_data, self._extensive_columns)

    @abstractmethod
    def _define_regions(self) -> None:
        ...

    def _build_street_network(self) -> None:
        self._street_network = gdf_to_nx(self._streets_data, extensive_columns=self._extensive_columns)

    def _build_topologies(self):
        topologies = defaultdict(nx.Graph)
        for u, v, data in self._street_network.edges(data=True):
            if not self._default_regions_in_topology and data.get(self._region_id_column) == self._default_region:
                continue
            topologies[data.get(self._region_id_column)].add_edge(u, v, **data)
        self._topologies = dict(topologies)

    def _topologies_connections_check(self):
        errors = []
        for region_id, region_topology in self._topologies.items():
            if not is_connected(region_topology):
                components = sorted(
                    nx.connected_components(region_topology), key=len, reverse=True
                )
                isolated_street_ids = []
                for component in components[1:]:
                    subgraph = region_topology.subgraph(component)
                    for _, _, data in subgraph.edges(data=True):
                        street_id = data.get(self._streets_id_column)
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
        self._to_project_crs()
        self._clean_streets_geometry()
        #self._streets_data.to_file("debug_streets_data.geojson", driver="GeoJSON")

        self._setup()
        self._define_regions()

        self._build_street_network()
        self._build_topologies()

        if self._check_topology_connections:
            self._topologies_connections_check()
        return TopologyBuildResult(self._street_network, self._topologies, self._streets_data)


class PolygonTopologyBuilder(TopologyBuilder):

    def __init__(self):
        super().__init__()
        self._polygons_data = None
        self._streets_geometry_column_name = ["geometry", "geom"]
        self._polygons_geometry_column_name = ["geometry", "geom"]

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
        if self._polygons_data.crs is None:
            raise ValueError("Polygons data has no CRS")

    def _to_project_crs(self) -> None:
        super()._to_project_crs()
        if self._polygons_data.crs != self._data_registry.crs:
            self._polygons_data = self._polygons_data.to_crs(self._data_registry.crs)

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
                if polygons_geometry_column_name in self._polygons_data:
                    self._polygons_geometry_column_name = polygons_geometry_column_name
                    logger.info("Using the name '%s' for the polygons_geometry_column_name",
                                polygons_geometry_column_name)
                    break
            else:
                raise ValueError("'polygons_geometry_column_name' does not exist")

    def _define_regions(self) -> None:
        violating_streets = []
        for street_index, street_geometry in zip(self._streets_data.index,
                                                 self._streets_data[self._streets_geometry_column_name]):
            street_geometry: shapely.MultiLineString
            for region_name, region_geometry in zip(self._polygons_data[self._region_id_column],
                                                    self._polygons_data[self._polygons_geometry_column_name]):
                region_geometry: shapely.MultiPolygon
                if not region_geometry.contains(street_geometry):
                    continue
                existing_region = self._streets_data.at[street_index, self._region_id_column]
                if existing_region != self._default_region:
                    street_id = self._streets_data.at[street_index, self._streets_id_column]
                    logger.error("Street %s lies within multiple polygons with id: %s and %s",
                                 street_id, existing_region, region_name)
                    violating_streets.append((street_id, existing_region, region_name))
                    continue
                self._streets_data.at[street_index, self._region_id_column] = region_name

        if violating_streets:
            details = ", ".join(
                f"street {street_id} (polygons {existing_region} and {region_name})"
                for street_id, existing_region, region_name in violating_streets
            )
            raise ValueError(f"{len(violating_streets)} street(s) lie within multiple polygons: {details}")


class SimpleTopologyBuilder(TopologyBuilder):

    def __init__(self):
        super().__init__()
        self._grouping = None

    def set_grouping(self, grouping: dict[str, dict[str, list[Any]]] | Path) -> Self:
        if isinstance(grouping, Path):
            grouping = load_yaml(grouping)
        self._grouping = grouping
        return self

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
