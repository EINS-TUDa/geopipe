"""DHN network and cost helpers.

Only owns graph-based DHN calculations shared by orchestrators.
"""

from __future__ import annotations

from math import isfinite
import networkx as nx

from pypeline.energy_system.core import Region


def _demand_nodes(
    graph: nx.Graph,
    demand_column: str = "total_heat_demand",
) -> set[tuple[float, float]]:
    """Return all nodes that are endpoints of demand-carrying edges."""
    nodes: set[tuple[float, float]] = set()
    for u, v, data in graph.edges(data=True):
        if data.get(demand_column, 0.0) > 0.0:
            nodes.add(u)
            nodes.add(v)
    return nodes


def build_district_heat_grid_from_topology(
    topology: nx.Graph,
    pipe_capex_eur_per_km: float,
    demand_column: str = "total_heat_demand",
    street_length_column: str = "street_length",
    street_id_column: str = "street_id",
) -> dict[str, float]:
    """Compute local DHN grid cost for one district from its topology graph."""
    seen: set = set()
    demand_length_m = 0.0
    for _, _, data in topology.edges(data=True):
        street_id = data.get(street_id_column)
        if street_id is not None and street_id in seen:
            continue
        if street_id is not None:
            seen.add(street_id)
        if data.get(demand_column, 0.0) > 0.0:
            demand_length_m += float(data.get(street_length_column, 0.0))

    min_pipe_km = demand_length_m / 1000.0
    return {
        "min_pipe_km": float(min_pipe_km),
        "local_grid_capex_base_eur": float(pipe_capex_eur_per_km * min_pipe_km),
    }


def build_inter_dhn_pipes_from_topologies(
    *,
    regions: list[Region],
    full_network: nx.Graph,
    pipe_capex_eur_per_km: float,
    demand_column: str = "total_heat_demand",
    min_interdistrict_pipe_length_m: float = 1.0,
) -> dict[tuple[int, int], dict[str, float]]:
    """Compute inter-district pipe specs using shortest path on the full street network."""
    capex_per_km = float(pipe_capex_eur_per_km)
    if not isfinite(capex_per_km) or capex_per_km < 0.0:
        raise ValueError("pipe_capex_eur_per_km must be finite and >= 0")
    if len(regions) < 2:
        return {}

    records: dict[tuple[int, int], dict[str, float]] = {}

    for i, region_a in enumerate(regions):
        for j, region_b in enumerate(regions):
            if j <= i:
                continue

            sources = _demand_nodes(region_a.topology, demand_column)
            targets = _demand_nodes(region_b.topology, demand_column)
            if not sources or not targets:
                continue

            try:
                dist = nx.multi_source_dijkstra_path_length(
                    full_network, sources=sources, weight="length"
                )
            except nx.NetworkXNoPath:
                continue

            reachable = {node: dist[node] for node in targets if node in dist}
            if not reachable:
                continue

            length_m = max(min(reachable.values()), float(min_interdistrict_pipe_length_m))
            min_pipe_km = length_m / 1000.0
            payload = {
                "min_pipe_km": float(min_pipe_km),
                "pipe_capex_base_eur": float(capex_per_km * min_pipe_km),
            }
            records[(region_a.id, region_b.id)] = payload
            records[(region_b.id, region_a.id)] = payload

    return records


__all__ = [
    "build_district_heat_grid_from_topology",
    "build_inter_dhn_pipes_from_topologies",
]
