# coding=utf-8
from typing import Optional, Any

import networkx as nx
import pandas as pd
from rasterio.crs import defaultdict


class Topology:

    def __init__(self, graph: nx.Graph):
        self.graph = graph

    @property
    def name(self):
        return self.graph.name

    def property_from_edges(self, property_name: str) -> list:
        """Return a list of the specified property from all edges in the graph."""
        return [edge[2][property_name] for edge in self.graph.edges(data=True)]

    def property_from_nodes(self, property_name: str) -> list:
        """Return a list of the specified property from all nodes in the graph."""
        return [node[1][property_name] for node in self.graph.nodes(data=True)]

    def sub_topologies_by_node_property(self, property_name: str) -> dict[Optional[str], 'Topology']:
        property_value_to_nodes = {}
        for node, data in self.graph.nodes(data=True):
            property_value_to_nodes.get(data.get(property_name), []).append(node)

        return {property_value: Topology(self.graph.subgraph(nodes))
                for property_value, nodes in property_value_to_nodes.items()}

    def sub_topology_by_node_property(self, property_name: str, property_value) -> 'Topology':
        nodes_with_property_value = [node for node, data in self.graph.nodes(data=True)
                                     if data.get(property_name) == property_value]
        return Topology(self.graph.subgraph(nodes_with_property_value))

    def sub_topologies_by_edge_property(self, property_name: str) -> dict[Any, 'Topology']:
        property_value_to_edges = defaultdict(list)
        for u, v, data in self.graph.edges(data=True):
            if not pd.isna(data.get(property_name)):
                property_value_to_edges[data.get(property_name)].append((u, v))
        return {property_value: Topology(self.graph.edge_subgraph(edges)) for property_value, edges in
                property_value_to_edges.items()}

    def sub_topology_by_edge_property(self, property_name: str, property_value) -> 'Topology':
        edges_with_property_value = [(u, v) for u, v, data in self.graph.edges(data=True)
                                     if data.get(property_name) == property_value]
        return Topology(self.graph.edge_subgraph(edges_with_property_value))



if __name__ == '__main__':
    G = nx.Graph(name="Test Topology")
    G.add_node(1, region="A")
    G.add_node(2, region="B")
    G.add_node(3, region="C")
    G.add_node(4, region="D")
    G.add_edge(1, 2, length=100, capacity=1000)
    G.add_edge(2, 3, length=150, capacity=1500)
    G.add_edge(3, 4, length=200, capacity=2000)

    topology = Topology(graph=G)
    print(f"Topology name: {topology.name}")

    for edge in G.edges(data=True):
        print(f"Edge: {edge}")
    for node, data in G.nodes(data=True):
        print(f"Node: {node}")
