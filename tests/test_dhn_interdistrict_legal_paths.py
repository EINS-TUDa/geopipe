import networkx as nx
import pytest

from pypeline.energy_system.core import Region
from pypeline.energy_system.dhn import build_inter_dhn_pipes_from_topologies
from pypeline.energy_system.region_connection import RegionConnection


def _region(region_id: int, u: tuple[float, float], v: tuple[float, float]) -> Region:
    topo = nx.Graph()
    topo.graph["id"] = region_id
    topo.add_edge(
        u,
        v,
        length=1.0,
        total_heat_demand=1.0,
        id=region_id,
    )
    return Region(region_id, topology=topo)


def _by_ids(connections: list[RegionConnection], id_in: int, id_out: int) -> RegionConnection | None:
    for rc in connections:
        if rc.region_id_in == id_in and rc.region_id_out == id_out:
            return rc
    return None


def no_third_region_path_t() -> None:
    """Checks pipes are not built when routing would require traversing a third region."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (4.0, 0.0), (5.0, 0.0))
    region_c = _region(2, (2.0, 0.0), (3.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=1.0, total_heat_demand=0.0, id=2)
    full.add_edge((2.0, 0.0), (3.0, 0.0), length=1.0, total_heat_demand=1.0, id=2)
    full.add_edge((3.0, 0.0), (4.0, 0.0), length=1.0, total_heat_demand=0.0, id=2)
    full.add_edge((4.0, 0.0), (5.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    connections = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b, region_c],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
    )

    assert _by_ids(connections, 0, 1) is None
    assert _by_ids(connections, 1, 0) is None
    assert _by_ids(connections, 0, 2) is not None
    assert _by_ids(connections, 2, 0) is not None
    assert _by_ids(connections, 1, 2) is not None
    assert _by_ids(connections, 2, 1) is not None


def unowned_connector_path_t() -> None:
    """Checks pipes can be built when regions are connected by unowned street segments."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (3.0, 0.0), (4.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((2.0, 0.0), (3.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((3.0, 0.0), (4.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    connections = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
    )

    assert _by_ids(connections, 0, 1) is not None
    assert _by_ids(connections, 1, 0) is not None


def short_path_free_t() -> None:
    """Checks short paths are marked below_distance_threshold when under threshold."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (3.0, 0.0), (4.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((2.0, 0.0), (3.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((3.0, 0.0), (4.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    connections = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
        below_distance_threshold_m=10.0,
    )

    rc_01 = _by_ids(connections, 0, 1)
    rc_10 = _by_ids(connections, 1, 0)
    assert rc_01 is not None and rc_10 is not None
    assert rc_01.pipe_length_km == pytest.approx(0.002)
    assert rc_01.below_distance_threshold is True
    assert rc_10.below_distance_threshold is True


def long_path_paid_t() -> None:
    """Checks long paths are not marked below_distance_threshold when above threshold."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (8.0, 0.0), (9.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=2.0, total_heat_demand=0.0, id=None)
    full.add_edge((2.0, 0.0), (3.0, 0.0), length=2.0, total_heat_demand=0.0, id=None)
    full.add_edge((3.0, 0.0), (4.0, 0.0), length=2.0, total_heat_demand=0.0, id=None)
    full.add_edge((4.0, 0.0), (5.0, 0.0), length=2.0, total_heat_demand=0.0, id=None)
    full.add_edge((5.0, 0.0), (6.0, 0.0), length=2.0, total_heat_demand=0.0, id=None)
    full.add_edge((6.0, 0.0), (7.0, 0.0), length=2.0, total_heat_demand=0.0, id=None)
    full.add_edge((7.0, 0.0), (8.0, 0.0), length=2.0, total_heat_demand=0.0, id=None)
    full.add_edge((8.0, 0.0), (9.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    connections = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
        below_distance_threshold_m=10.0,
    )

    rc_01 = _by_ids(connections, 0, 1)
    rc_10 = _by_ids(connections, 1, 0)
    assert rc_01 is not None and rc_10 is not None
    assert rc_01.pipe_length_km == pytest.approx(0.014)
    assert rc_01.below_distance_threshold is False
    assert rc_10.below_distance_threshold is False


def touching_free_t() -> None:
    """Checks touching regions are always marked below_distance_threshold."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (1.0, 0.0), (2.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    connections = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
        below_distance_threshold_m=0.0,
    )

    rc_01 = _by_ids(connections, 0, 1)
    assert rc_01 is not None
    assert rc_01.below_distance_threshold is True
