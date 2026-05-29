# coding=utf-8
"""Tests for the polygon-based topology builder."""

from geopipe.topology_builder import TopologyBuildResult
from geopipe.topology_builder.topology_builder import PolygonTopologyBuilder

def test_polygon_topology_builder(streets_data_path, polygons_data_path):
    builder = PolygonTopologyBuilder()
    (builder
         .set_streets_data(streets_data_path)
         .set_polygons_data(polygons_data_path)
         .set_region_id_column("id")
         .set_default_region(None)
         .set_streets_geometry_column_name("geometry")
         .set_polygons_geometry_column_name("geometry"))

    build_result = builder.build()
    assert isinstance(build_result, TopologyBuildResult)
    for region_name in range(9):
        assert region_name in build_result.streets["id"]
