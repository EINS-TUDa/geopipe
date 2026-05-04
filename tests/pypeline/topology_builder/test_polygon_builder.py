# coding=utf-8
"""Tests for the polygon-based topology builder."""

from pypeline.topology_builder import TopologyBuildResult
from pypeline.topology_builder.polygon_builder import build_topology_from_polygons

from pypeline.topology_builder.topology_builder import PolygonTopologyBuilder


def test_polygon_builder(streets_data_path, polygons_data_path):
    build_result = build_topology_from_polygons(streets_data_path, polygons_data_path, region_id_column='id',
                                                default_region=None, streets_geometry_column_name="geometry",
                                                polygons_geometry_column_name="geometry")

    assert isinstance(build_result, TopologyBuildResult)
    for region_name in range(9):
        assert region_name in build_result.streets["id"]


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
