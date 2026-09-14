import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import box

from geopipe.data.data_registry import DataRegistry, DataRegistryQuery
from geopipe.data.dataset import SimpleDataset
from geopipe.energy_system.units import UnitEnum
from geopipe.topology_builder.topology import Topology

CRS = "EPSG:25832"
QUERY = DataRegistryQuery(key="k")


def _topology(x0: float) -> Topology:
    """A single 10 m street starting at (x0, 0)."""
    graph = nx.Graph()
    graph.graph["crs"] = CRS
    graph.add_edge((x0, 0.0), (x0 + 10.0, 0.0))
    return Topology(graph)


def _scope() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[box(-1, -1, 50, 1)], crs=CRS)


def _registry() -> DataRegistry:
    registry = DataRegistry(crs=CRS)
    registry.register(SimpleDataset(keys=["k"], data="local", priority=20, scope=_scope()))
    registry.register(SimpleDataset(keys=["k"], data="fallback", priority=10))
    return registry


def test_region_is_routed_to_best_covering_dataset():
    registry = _registry()
    assert registry.query(_topology(0), QUERY) == "local"
    assert registry.query(_topology(100), QUERY) == "fallback"


def test_partially_covered_region_goes_to_next_dataset():
    # the street spans x = 45..55, the scope ends at x = 50
    assert _registry().query(_topology(45), QUERY) == "fallback"


def test_uncovered_region_raises():
    registry = DataRegistry(crs=CRS)
    registry.register(SimpleDataset(keys=["k"], data="local", scope=_scope()))
    with pytest.raises(LookupError, match="covers"):
        registry.query(_topology(100), QUERY)


def test_result_is_converted_to_the_requested_unit():
    registry = DataRegistry(crs=CRS)
    registry.register(SimpleDataset(keys=["k"], data=2500.0, unit=UnitEnum.KWH))
    assert registry.query(_topology(0), QUERY, unit=UnitEnum.MWH) == pytest.approx(2.5)


def test_missing_unit_is_rejected_when_a_unit_is_requested():
    registry = DataRegistry(crs=CRS)
    registry.register(SimpleDataset(keys=["k"], data=1.0))
    with pytest.raises(ValueError, match="no unit"):
        registry.query(_topology(0), QUERY, unit=UnitEnum.MWH)
