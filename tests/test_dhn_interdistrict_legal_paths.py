import networkx as nx

from pypeline.energy_system.core import Region
from pypeline.energy_system.dhn import build_inter_dhn_pipes_from_topologies


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

    specs = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b, region_c],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
    )

    assert (0, 1) not in specs
    assert (1, 0) not in specs
    assert (0, 2) in specs
    assert (2, 0) in specs
    assert (1, 2) in specs
    assert (2, 1) in specs


def unowned_connector_path_t() -> None:
    """Checks pipes can be built when regions are connected by unowned street segments."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (3.0, 0.0), (4.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((2.0, 0.0), (3.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((3.0, 0.0), (4.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    specs = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
    )

    assert (0, 1) in specs
    assert (1, 0) in specs


def short_path_free_t() -> None:
    """Checks short legal paths are marked free when under threshold M."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (3.0, 0.0), (4.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((2.0, 0.0), (3.0, 0.0), length=1.0, total_heat_demand=0.0, id=None)
    full.add_edge((3.0, 0.0), (4.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    specs = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
        free_pipe_max_length_m=10.0,
    )

    assert specs[(0, 1)]["path_length_m_raw"] == 2.0
    assert specs[(0, 1)]["is_free"] is True
    assert specs[(1, 0)]["is_free"] is True


def long_path_paid_t() -> None:
    """Checks long legal paths remain paid when above threshold M."""
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

    specs = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
        free_pipe_max_length_m=10.0,
    )

    assert specs[(0, 1)]["path_length_m_raw"] == 14.0
    assert specs[(0, 1)]["is_free"] is False
    assert specs[(1, 0)]["is_free"] is False


def touching_free_t() -> None:
    """Checks touching regions are always marked free regardless of threshold."""
    region_a = _region(0, (0.0, 0.0), (1.0, 0.0))
    region_b = _region(1, (1.0, 0.0), (2.0, 0.0))

    full = nx.Graph()
    full.add_edge((0.0, 0.0), (1.0, 0.0), length=1.0, total_heat_demand=1.0, id=0)
    full.add_edge((1.0, 0.0), (2.0, 0.0), length=1.0, total_heat_demand=1.0, id=1)

    specs = build_inter_dhn_pipes_from_topologies(
        regions=[region_a, region_b],
        full_network=full,
        pipe_capex_eur_per_km=1_000.0,
        free_pipe_max_length_m=0.0,
    )

    assert specs[(0, 1)]["is_touching"] is True
    assert specs[(0, 1)]["path_length_m_raw"] == 0.0
    assert specs[(0, 1)]["is_free"] is True
