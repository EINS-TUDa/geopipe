# coding=utf-8
from __future__ import annotations
from typing import Optional, Iterable, Iterator
from shapely.geometry import MultiPoint
import networkx as nx

from geopipe.energy_system.demand import Demand
from geopipe.energy_system.technology import (Technology, CentralTechnology, DecentralTechnology,
                                              CHPTechnology, GridTechnology)
from geopipe.topology_builder.topology import Topology

class Region:
    def __init__(self, id_: int, topology: Topology, demands: Iterable[Demand],
                 technologies: Iterable[Technology]):
        self._id = id_
        self._topology = topology
        self._demands = {demand.name: demand for demand in demands}
        self._technologies = {"decentral": [], "central": [], "grid": []}
        class_to_key = {DecentralTechnology: "decentral", CentralTechnology: "central", CHPTechnology: "central",
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

    def add_connection(self, region_id_1: int, region_id_2: int, length_m: float, connection_graph: nx.Graph) -> None:
        self._connections[self._connection_id(region_id_1, region_id_2)] = (length_m, connection_graph)

    def check_connection(self, region_id_1: int, region_id_2: int) -> Optional[tuple[float, nx.Graph]]:
        return self._connections.get(self._connection_id(region_id_1, region_id_2), None)

    def __iter__(self) -> Iterator[tuple[tuple[int, int], tuple[float, nx.Graph]]]:
        for (region_id_1, region_id_2), data in self._connections.items():
            yield (region_id_1, region_id_2), data

def compute_region_connections(region_to_topology: dict[int, 'Topology'],
                               full_topology: Topology,
                               region_id_column: str,
                               connection_over_other_region: bool = False) -> RegionConnections:
    region_connections: RegionConnections = RegionConnections()

    edge_owner_map = {(u, v): data.get(region_id_column) for u, v, data in full_topology.graph.edges(data=True)}

    for a, (region_id_a, topology_a) in enumerate(region_to_topology.items()):
        for b, (region_id_b, topology_b) in enumerate(region_to_topology.items()):
            if b <= a:
                continue

            nodes_a = set(topology_a.graph.nodes)
            nodes_b = set(topology_b.graph.nodes)

            if not nodes_a or not nodes_b:  # Empty topology
                continue

            intersection = nodes_a.intersection(nodes_b)

            if intersection:
                length_m = 0.0
                connection_graph = nx.Graph()
                connection_graph.add_nodes_from(intersection)
            else:
                (length, path) = nx.multi_source_dijkstra(full_topology.graph,
                                                            sources=nodes_a,
                                                            weight="length")

                length: dict  # key: node, value: minimum length from source nodes to this node. No entry means there is no connection
                length_to_nodes_b = {node_b: length.get(node_b) for node_b in nodes_b if length.get(node_b) is not None}
                if not length_to_nodes_b:  # No connection
                    continue

                target_node = min(length_to_nodes_b, key=length_to_nodes_b.get)
                length_m = length_to_nodes_b[target_node]
                connection_graph = nx.Graph()
                shortest_path = path[target_node]
                assert len(shortest_path) >= 2  # == 1 would mean touching and this is handled in the previous case
                edges_on_shortest_path = tuple(zip(shortest_path[:-1], shortest_path[1:]))
                if not connection_over_other_region:
                    permitted_regions = {None, region_id_a, region_id_b}
                    other_region_used = False
                    for u, v in edges_on_shortest_path:
                        owner = edge_owner_map.get((u, v), edge_owner_map.get((v, u)))
                        if owner not in permitted_regions:
                            other_region_used = True
                            break
                    if other_region_used:  # not allowed
                        continue

                connection_graph.add_edges_from(zip(shortest_path[:-1], shortest_path[1:]))

            region_connections.add_connection(region_id_a, region_id_b, length_m, connection_graph)

    return region_connections
