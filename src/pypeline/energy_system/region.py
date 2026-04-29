# coding=utf-8
from __future__ import annotations
from typing import Optional, Iterable, Iterator
from shapely.geometry import MultiPoint
import networkx as nx

from pypeline.energy_system.demand import Demand
from pypeline.energy_system.technology import (Technology, CentralTechnology, DecentralTechnology,
                                               CHPTechnology, GridTechnology)
from pypeline.topology_builder.topology import Topology

class Region:
    def __init__(self, id_: int, topology: Topology, demands: Iterable[Demand],
                 technologies: Iterable[Technology]):
        self._id = id_
        self._topology = topology
        self._demands = {demand.name: demand for demand in demands}
        self._technologies = {"decentral": [], "central": [], "chp": [], "grid": []}
        class_to_key = {DecentralTechnology: "decentral", CentralTechnology: "central", CHPTechnology: "chp",
                        GridTechnology: "grid"}
        for technology in technologies:
            self._technologies[class_to_key[type(technology)]].append(technology)

    @property
    def id(self) -> int:
        return self._id

    @property
    def topology(self) -> Topology:
        return self._topology

    @property
    def boundary(self):
        """Convex hull of topology nodes, usable as a polygon geometry for visualisation."""
        nodes = list(self._topology.graph.nodes)
        if not nodes:
            raise ValueError(f"Region {self.id} has no topology nodes")
        return MultiPoint(nodes).convex_hull

    @property
    def crs(self):
        return self._topology.graph.graph.get("crs")

    def demand(self, name: str) -> Optional[Demand]:
        return self._demands.get(name, None)

    @property
    def demands(self) -> tuple[Demand, ...]:
        return tuple(self._demands.values())

    @property
    def decentral_techs(self) -> tuple[DecentralTechnology, ...]:
        return tuple(self._technologies["decentral"])

    @property
    def central_techs(self) -> tuple[CentralTechnology, ...]:
        return tuple(self._technologies["central"])

    @property
    def chps(self) -> tuple[CHPTechnology, ...]:
        return tuple(self._technologies["chp"])

    @property
    def grids(self) -> tuple[GridTechnology, ...]:
        return tuple(self._technologies["grid"])


class RegionConnections:
    def __init__(self):
        self._connections = {}

    @staticmethod
    def _connection_id(region_id_1: int, region_id_2: int) -> tuple[int, int]:
        if region_id_1 == region_id_2:
            raise ValueError("Same region")
        return (region_id_1, region_id_2) if region_id_1 < region_id_2 else (region_id_2, region_id_1)

    def add_connection(self, region_id_1: int, region_id_2: int, length_m: float) -> None:
        self._connections[self._connection_id(region_id_1, region_id_2)] = length_m

    def check_connection(self, region_id_1: int, region_id_2: int) -> Optional[float]:
        return self._connections.get(self._connection_id(region_id_1, region_id_2), None)

    def __iter__(self) -> Iterator[Region]:
        for (region_id_1, region_id_2), length_m in self._connections.items():
            yield region_id_1, region_id_2, length_m


def _legal_pair_network(full_network: nx.Graph,
                        region_a_id: int,
                        region_b_id: int,
                        edge_owner_by_pair: dict[tuple[int, int], int | None],
                        ) -> nx.Graph:
    """Returns legal pipe-routing edges for a region pair.

    Legal edges are unowned streets and streets owned by the region pair.
    Edges owned by a third region are excluded.
    """
    allowed_owners = {None, region_a_id, region_b_id}

    def _allow_edge(u, v) -> bool:
        owner = edge_owner_by_pair.get((u, v))
        return owner in allowed_owners

    return nx.subgraph_view(full_network, filter_edge=_allow_edge)


def compute_region_connections(region_to_topology: dict[int, 'Topology'],
                               full_topology: Topology,
                               region_id_column: str) -> RegionConnections:
    region_connections: RegionConnections = RegionConnections()

    edge_owner_map = {(u, v): data.get(region_id_column) for u, v, data in full_topology.graph.edges(data=True)}

    for a, (region_id_a, topology_a) in enumerate(region_to_topology.items()):
        for b, (region_id_b, topology_b) in enumerate(region_to_topology.items()):
            if b <= a:
                continue

            nodes_a = set(topology_a.graph.nodes)
            nodes_b = set(topology_b.graph.nodes)
            touching = bool(nodes_a.intersection(nodes_b))

            if touching:
                length_m = 0.0
            else:
                if not nodes_a or not nodes_b:
                    continue

                legal_network = _legal_pair_network(full_topology.graph,
                                                    region_a_id=region_id_a,
                                                    region_b_id=region_id_b,
                                                    edge_owner_by_pair=edge_owner_map, )

                dist = nx.multi_source_dijkstra_path_length(legal_network,
                                                            sources=nodes_a,
                                                            weight="length")

                distances_between_regions = {dist.get(node_b) for node_b in nodes_b}
                distances_between_regions.discard(None)

                if not distances_between_regions:  # Distances is empty -> regions are not connected
                    continue

                length_m = min(distances_between_regions)

            region_connections.add_connection(region_id_a, region_id_b, length_m)

    return region_connections
