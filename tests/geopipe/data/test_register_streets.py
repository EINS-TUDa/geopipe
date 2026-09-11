import geopandas as gpd
import pytest
from shapely import LineString
from shapely.geometry import box

from geopipe.data.data_registry import DataKeys, DataRegistry
from geopipe.data.dataset import SimpleDataset
from geopipe.topology_builder.topology import SOURCE_STREET_ID, SOURCE_SHARE

CRS = "EPSG:25832"


def _streets(x0: float = 0.0, ids=("A", "B")) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({
        "fid": list(ids),
        "geometry": [LineString([(x0, 0), (x0 + 10, 0)]), LineString([(x0 + 10, 0), (x0 + 20, 0)])],
    }, geometry="geometry", crs=CRS)


def _area(x_min: float, x_max: float) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[box(x_min, -1, x_max, 1)], crs=CRS)


def test_streets_get_their_original_id_and_length_share():
    registry = DataRegistry(crs=CRS)
    registry.register_streets(_streets(), id_column="fid")
    streets = registry.streets(_area(0, 20))
    assert list(streets[SOURCE_STREET_ID]) == ["A", "B"]
    assert list(streets[SOURCE_SHARE]) == [1.0, 1.0]


def test_streets_can_only_be_registered_with_register_streets():
    with pytest.raises(ValueError, match="register_streets"):
        DataRegistry(crs=CRS).register(SimpleDataset(keys=[DataKeys.STREET_NETWORK], data=_streets()))


def test_duplicate_street_ids_are_rejected():
    with pytest.raises(ValueError, match="not unique"):
        DataRegistry(crs=CRS).register_streets(_streets(ids=("A", "A")), id_column="fid")


def test_area_is_routed_to_the_best_covering_street_dataset():
    registry = DataRegistry(crs=CRS)
    registry.register_streets(_streets(ids=("local1", "local2")), id_column="fid", scope=_area(-5, 50),
                              priority=20)
    registry.register_streets(_streets(ids=("global1", "global2")), id_column="fid")
    assert list(registry.streets(_area(0, 20))["fid"]) == ["local1", "local2"]
    # an area reaching beyond the local scope gets the global streets
    assert list(registry.streets(_area(0, 100))["fid"]) == ["global1", "global2"]
