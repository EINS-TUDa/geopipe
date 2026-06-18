# coding=utf-8
"""Tests for splitting street segments at junctions and crossings."""

import geopandas as gpd
import networkx as nx
import pytest
from shapely import LineString, MultiLineString

from geopipe.topology_builder.topology_build_utils import (
    divide_segments_at_junctions, gdf_to_nx)

# real street data stores each feature as a single-component MultiLineString, so every
# case is exercised with bare LineStrings and with MultiLineString-wrapped geometries
GEOM_TYPES = pytest.mark.parametrize("multi", [False, True], ids=["linestring", "multilinestring"])


def _streets(rows, *, multi=False):
    if multi:
        rows = {**rows, "geometry": [MultiLineString([g]) for g in rows["geometry"]]}
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:25832")


def _t_junction_streets(multi=False):
    """Street ``A`` runs straight with vertices only at its ends; street ``B`` ends on
    ``A``'s interior at (40, 0) -> a T-junction with no shared vertex."""
    return _streets({
        "fid": ["A", "B"],
        "waerme_mwh": [100.0, 50.0],
        "geometry": [
            LineString([(0, 0), (100, 0)]),
            LineString([(40, 10), (40, 0)]),
        ],
    }, multi=multi)


@GEOM_TYPES
def test_t_junction_is_disconnected_without_division(multi):
    streets = _t_junction_streets(multi)
    graph = gdf_to_nx(streets, extensive_columns=["waerme_mwh"])
    assert not nx.is_connected(graph)


@GEOM_TYPES
def test_t_junction_splits_crossed_street_and_connects(multi):
    streets = _t_junction_streets(multi)
    split = divide_segments_at_junctions(
        streets, extensive_columns=["waerme_mwh"], id_column="fid")

    # crossed street A is cut into two distinctly-identified rows; B is left whole
    assert sorted(split["fid"]) == ["100A", "200A", "B"]

    graph = gdf_to_nx(split, extensive_columns=["waerme_mwh"])
    assert nx.is_connected(graph)


@GEOM_TYPES
def test_extensive_column_split_by_length_share(multi):
    streets = _t_junction_streets(multi)
    split = divide_segments_at_junctions(
        streets, extensive_columns=["waerme_mwh"], id_column="fid")

    demands = dict(zip(split["fid"], split["waerme_mwh"]))
    # A (100 m, demand 100) cut at 40 m -> 40 / 60 split that sums back to the original
    assert demands["100A"] == 40.0
    assert demands["200A"] == 60.0
    assert demands["100A"] + demands["200A"] == 100.0
    assert demands["B"] == 50.0


@GEOM_TYPES
def test_geometry_type_is_preserved(multi):
    streets = _t_junction_streets(multi)
    split = divide_segments_at_junctions(
        streets, extensive_columns=["waerme_mwh"], id_column="fid")
    expected = "MultiLineString" if multi else "LineString"
    assert set(split.geom_type) == {expected}


@GEOM_TYPES
def test_mid_segment_crossing_splits_both_streets(multi):
    """Two streets crossing in each other's interior, with no vertex at the crossing."""
    streets = _streets({
        "fid": ["A", "B"],
        "waerme_mwh": [100.0, 80.0],
        "geometry": [
            LineString([(0, 0), (100, 0)]),
            LineString([(50, -50), (50, 50)]),
        ],
    }, multi=multi)

    graph_before = gdf_to_nx(streets, extensive_columns=["waerme_mwh"])
    assert not nx.is_connected(graph_before)

    split = divide_segments_at_junctions(
        streets, extensive_columns=["waerme_mwh"], id_column="fid")

    # both streets are cut at the crossing -> four distinctly-identified rows
    assert sorted(split["fid"]) == ["100A", "100B", "200A", "200B"]

    graph = gdf_to_nx(split, extensive_columns=["waerme_mwh"])
    assert nx.is_connected(graph)


@GEOM_TYPES
def test_existing_vertex_junction_is_still_split(multi):
    """Even when the crossed street already has a vertex at the junction, it is cut into
    distinctly-identified rows so the resulting segments are addressable."""
    streets = _streets({
        "fid": ["A", "B"],
        "waerme_mwh": [100.0, 50.0],
        "geometry": [
            LineString([(0, 0), (40, 0), (100, 0)]),
            LineString([(40, 10), (40, 0)]),
        ],
    }, multi=multi)
    split = divide_segments_at_junctions(
        streets, extensive_columns=["waerme_mwh"], id_column="fid")
    assert sorted(split["fid"]) == ["100A", "200A", "B"]


@GEOM_TYPES
def test_shared_endpoint_is_not_split(multi):
    """Streets that already meet at a shared endpoint connect without any division."""
    streets = _streets({
        "fid": ["A", "B"],
        "waerme_mwh": [100.0, 50.0],
        "geometry": [
            LineString([(0, 0), (50, 0)]),
            LineString([(50, 0), (50, 50)]),
        ],
    }, multi=multi)
    split = divide_segments_at_junctions(
        streets, extensive_columns=["waerme_mwh"], id_column="fid")
    assert sorted(split["fid"]) == ["A", "B"]


@GEOM_TYPES
def test_no_junction_leaves_streets_untouched(multi):
    """A bend vertex with no other street touching it is not a junction."""
    streets = _streets({
        "fid": ["A", "B"],
        "waerme_mwh": [100.0, 50.0],
        "geometry": [
            LineString([(0, 0), (50, 0), (50, 50)]),  # an L-shaped street with a bend
            LineString([(200, 200), (300, 200)]),      # a far-away unrelated street
        ],
    }, multi=multi)
    split = divide_segments_at_junctions(
        streets, extensive_columns=["waerme_mwh"], id_column="fid")
    assert sorted(split["fid"]) == ["A", "B"]