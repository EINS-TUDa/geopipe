import geopandas as gpd
import pytest
from shapely import LineString
from shapely.geometry import box

from geopipe.data.data_registry import DataRegistry
from geopipe.topology_builder.topology import REGION_ID
from geopipe.topology_builder.topology_builder import PolygonTopologyBuilder

CRS = "EPSG:25832"


def _registry() -> DataRegistry:
    """Street ``a`` lies in polygon 1, ``b`` in polygon 2, ``c`` crosses the boundary between them."""
    streets = gpd.GeoDataFrame({
        "fid": ["a", "b", "c"],
        "geometry": [LineString([(1, 1), (4, 1)]), LineString([(6, 1), (9, 1)]), LineString([(4, 1), (6, 1)])],
    }, geometry="geometry", crs=CRS)
    registry = DataRegistry(crs=CRS)
    registry.register_streets(streets, id_column="fid")
    return registry


def _polygons(*boxes) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"id": list(range(1, len(boxes) + 1))}, geometry=list(boxes), crs=CRS)


def test_streets_within_a_polygon_get_its_region():
    result = (PolygonTopologyBuilder()
              .set_data_registry(_registry())
              .set_polygons_data(_polygons(box(0, 0, 5, 2), box(5, 0, 10, 2)), id_column="id")
              .build())
    regions = dict(zip(result.streets["fid"], result.streets[REGION_ID]))
    assert regions["a"] == 1 and regions["b"] == 2
    assert regions["c"] is None  # crosses the boundary
    assert set(result.region_topologies) == {1, 2}
    # the crossing street stays in the network, e.g. for connections between regions
    assert result.network.number_of_edges() == 3


def test_street_within_overlapping_polygons_raises():
    builder = (PolygonTopologyBuilder()
               .set_data_registry(_registry())
               .set_polygons_data(_polygons(box(0, 0, 5, 2), box(0, 0, 10, 2)), id_column="id"))
    with pytest.raises(ValueError, match="multiple polygons"):
        builder.build()
