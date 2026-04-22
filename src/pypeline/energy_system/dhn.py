"""DHN network and cost helpers.

Only owns graph-based DHN calculations shared by orchestrators.
"""

from __future__ import annotations
from collections.abc import Hashable
from math import isfinite
import networkx as nx
from pypeline.energy_system.core import Region
from pypeline.energy_system.pipe import Pipe


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


def _normalize_region_owner(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not isfinite(value):
        return None
    return int(value)


def _edge_owner_by_pair(
    full_network: nx.Graph,
    *,
    region_id_column: str,
) -> dict[frozenset[Hashable], int | None]:
    owners: dict[frozenset[Hashable], int | None] = {}
    for u, v, data in full_network.edges(data=True):
        owners[frozenset((u, v))] = _normalize_region_owner(data.get(region_id_column))
    return owners


def _legal_pair_network(
    full_network: nx.Graph,
    *,
    region_a_id: int,
    region_b_id: int,
    edge_owner_by_pair: dict[frozenset[Hashable], int | None],
) -> nx.Graph:
    """Returns legal pipe-routing edges for a region pair.

    Legal edges are unowned streets and streets owned by the region pair. 
    Edges owned by a third region are excluded.
    """
    allowed_owners = {None, int(region_a_id), int(region_b_id)}

    def _allow_edge(u: Hashable, v: Hashable, _allowed: set[int | None] = allowed_owners) -> bool:
        owner = edge_owner_by_pair.get(frozenset((u, v)))
        return owner in _allowed

    return nx.subgraph_view(full_network, filter_edge=_allow_edge)


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
    region_id_column: str = "id",
    min_interdistrict_pipe_length_m: float = 1.0,
    below_distance_threshold_m: float | None = None,
    pipe_loss_fraction: float = 0.02,
    pipe_capex_eur_per_mw: float = 0.0,
    pipe_opex_eur_per_mwh: float = 0.0,
    pipe_cap_max_mw: float = 500.0,
    pipe_lifetime_years: int = 40,
) -> list[Pipe]:
    """Compute inter-district pipe connections using legal shortest paths on the street network."""
    capex_per_km = float(pipe_capex_eur_per_km)
    if not isfinite(capex_per_km) or capex_per_km < 0.0:
        raise ValueError("pipe_capex_eur_per_km must be finite and >= 0")
    if below_distance_threshold_m is not None:
        threshold = float(below_distance_threshold_m)
        if not isfinite(threshold) or threshold < 0.0:
            raise ValueError("below_distance_threshold_m must be finite and >= 0 when provided")
    else:
        threshold = None
    if len(regions) < 2:
        return []

    connections: list[Pipe] = []
    edge_owner_map = _edge_owner_by_pair(full_network, region_id_column=region_id_column)

    for i, region_a in enumerate(regions):
        for j, region_b in enumerate(regions):
            if j <= i:
                continue

            sources = _demand_nodes(region_a.topology, demand_column)
            targets = _demand_nodes(region_b.topology, demand_column)
            if not sources or not targets:
                continue

            legal_network = _legal_pair_network(
                full_network,
                region_a_id=int(region_a.id),
                region_b_id=int(region_b.id),
                edge_owner_by_pair=edge_owner_map,
            )

            try:
                dist = nx.multi_source_dijkstra_path_length(
                    legal_network,
                    sources=sources,
                    weight="length",
                )
            except nx.NetworkXNoPath:
                continue

            reachable = {node: dist[node] for node in targets if node in dist}
            if not reachable:
                continue

            raw_length_m = float(min(reachable.values()))
            used_length_m = max(raw_length_m, float(min_interdistrict_pipe_length_m))
            touching = bool(set(region_a.topology.nodes).intersection(set(region_b.topology.nodes)))
            below_threshold = touching or (threshold is not None and raw_length_m <= threshold + 1e-9)

            pipe_length_km = used_length_m / 1000.0
            pipe_capex_base = capex_per_km * pipe_length_km

            for id_in, id_out in [(region_a.id, region_b.id), (region_b.id, region_a.id)]:
                connections.append(Pipe(
                    region_id_in=id_in,
                    region_id_out=id_out,
                    pipe_length_km=pipe_length_km,
                    pipe_capex_base_eur=pipe_capex_base,
                    below_distance_threshold=below_threshold,
                    pipe_loss_fraction=pipe_loss_fraction,
                    pipe_capex_eur_per_mw=pipe_capex_eur_per_mw,
                    pipe_opex_eur_per_mwh=pipe_opex_eur_per_mwh,
                    pipe_cap_max_mw=pipe_cap_max_mw,
                    pipe_lifetime_years=pipe_lifetime_years,
                ))

    return connections


__all__ = [
    "build_district_heat_grid_from_topology",
    "build_inter_dhn_pipes_from_topologies",
]
