import geopandas as gpd
import pytest
from shapely import LineString
from shapely.geometry import box

from geopipe.data.data_registry import DataRegistry, DataRegistryQuery
from geopipe.data.dataset import StreetValueDataset
from geopipe.topology_builder.topology import Topology
from geopipe.topology_builder.topology_builder import SimpleTopologyBuilder

CRS = "EPSG:25832"
QUERY = DataRegistryQuery(key="heat")


def _streets() -> gpd.GeoDataFrame:
    """Street ``A`` (100 m, two segments) is crossed at x = 40 by ``B``, which ends on it (T-junction)."""
    return gpd.GeoDataFrame({
        "fid": ["A", "B"],
        "waerme_mwh": [100.0, 50.0],
        "geometry": [LineString([(0, 0), (70, 0), (100, 0)]), LineString([(40, 10), (40, 0)])],
    }, geometry="geometry", crs=CRS)


def test_values_follow_split_streets_into_regions():
    streets = _streets()
    registry = DataRegistry(crs=CRS)
    # junction division splits A into 100A (x = 0..40) and 200A (x = 40..100)
    registry.register_streets(streets, id_column="fid", divide_at_junctions=True)
    registry.register(StreetValueDataset.from_column(streets, id_column="fid", value_column="waerme_mwh",
                                                     keys=["heat"]))
    result = (SimpleTopologyBuilder()
              .set_data_registry(registry)
              .set_area(gpd.GeoDataFrame(geometry=[box(-10, -10, 110, 20)], crs=CRS))
              .set_grouping({1: {"fid": ["100A", "B"]}, 2: {"fid": ["200A"]}})
              .build())
    topologies = {region_id: Topology(graph) for region_id, graph in result.region_topologies.items()}
    assert registry.query(topologies[1], QUERY) == pytest.approx(40.0 + 50.0)
    assert registry.query(topologies[2], QUERY) == pytest.approx(60.0)


def test_duplicate_street_ids_are_rejected():
    streets = _streets().assign(fid=["A", "A"])
    with pytest.raises(ValueError, match="not unique"):
        StreetValueDataset.from_column(streets, id_column="fid", value_column="waerme_mwh", keys=["heat"])
