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
