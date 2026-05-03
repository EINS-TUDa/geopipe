# coding=utf-8
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional
import geopandas as gpd
import networkx as nx

import logging

import shapely

from pypeline.topology_builder import TopologyBuildResult
from .core import gdf_to_nx
logger = logging.getLogger(__name__)


def build_topology_from_polygons(streets_data: gpd.GeoDataFrame | Path,
                                 polygons_data: gpd.GeoDataFrame | Path,
                                 region_id_column: str,
                                 default_region: Any = None,
                                 streets_geometry_column_name: Optional[str] = None,
                                 polygons_geometry_column_name: Optional[str] = None) -> TopologyBuildResult:
    """Build a topology according to the specified polygons."""
    if isinstance(streets_data, Path):
        streets_data = gpd.read_file(streets_data)
    streets_data: gpd.GeoDataFrame

    if isinstance(polygons_data, Path):
        polygons_data = gpd.read_file(polygons_data)
    polygons_data: gpd.GeoDataFrame

    if polygons_data.crs != streets_data.crs:
        polygons_data = polygons_data.to_crs(streets_data.crs)

    streets_data[region_id_column] = default_region
    for street_index, street_geometry in zip(streets_data.index, streets_data[streets_geometry_column_name]):
        street_geometry: shapely.MultiLineString
        for region_name, region_geometry in zip(polygons_data[region_id_column], polygons_data[polygons_geometry_column_name]):
            region_geometry: shapely.MultiPolygon
            if not region_geometry.contains(street_geometry):
                continue
            if streets_data.at[street_index, region_id_column] != default_region:
                logger.error("Street at index %d lies within multiple polygons with id: %d and %d",
                             street_index, streets_data.at[street_index, region_id_column], region_name)
                raise ValueError("Street lies within multiple polygons")
            streets_data.at[street_index, region_id_column] = region_name

    street_network = gdf_to_nx(streets_data)

    topologies = defaultdict(nx.Graph)
    for u, v, data in street_network.edges(data=True):
        topologies[data.get(region_id_column)].add_edge(u, v, **data)

    # Todo: Check if the street segments of a region are connected
    return TopologyBuildResult(network=street_network,
                               region_topologies=topologies,
                               streets=streets_data)
