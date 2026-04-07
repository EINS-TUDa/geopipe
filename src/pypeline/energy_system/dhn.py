from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING

import geopandas as gpd  # used by gdf_to_nx and gdf_to_region_topologies
import networkx as nx

if TYPE_CHECKING:
    from pypeline.energy_system.region import Region


def gdf_to_nx(gdf: gpd.GeoDataFrame) -> nx.Graph:
    """Convert a GeoDataFrame of line segments to a NetworkX graph.

    Nodes are (x, y) coordinate tuples. Each pair of consecutive coordinates in a
    LineString becomes a graph edge carrying all non-geometry attributes of the parent
    row plus a ``length`` attribute (Euclidean distance in CRS units). The graph CRS
    is stored in ``G.graph['crs']``.

    MultiLineString geometries are split into their constituent LineStrings; all
    sub-edges inherit the row's attributes unchanged (use ``street_id`` to
    deduplicate attribute aggregations when needed).
    """
    G: nx.Graph = nx.Graph()
    if hasattr(gdf, "crs") and gdf.crs is not None:
        G.graph["crs"] = gdf.crs

    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue

        attrs = {col: row[col] for col in gdf.columns if col != "geometry"}

        if geom.geom_type == "LineString":
            lines = [geom]
        elif geom.geom_type == "MultiLineString":
            lines = list(geom.geoms)
        else:
            continue

        for line in lines:
            coords = list(line.coords)
            for i in range(len(coords) - 1):
                u = (float(coords[i][0]), float(coords[i][1]))
                v = (float(coords[i + 1][0]), float(coords[i + 1][1]))
                length = ((v[0] - u[0]) ** 2 + (v[1] - u[1]) ** 2) ** 0.5
                G.add_edge(u, v, geometry=line, length=length, **attrs)

    return G


def _demand_nodes(
    G: nx.Graph,
    demand_column: str = "total_heat_demand",
) -> set[tuple[float, float]]:
    """Return all nodes that are endpoints of demand-carrying edges."""
    nodes: set[tuple[float, float]] = set()
    for u, v, d in G.edges(data=True):
        if d.get(demand_column, 0.0) > 0.0:
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
    """Compute local DHN grid cost for one district from its topology graph.

    Sums ``street_length`` of demand-carrying segments, deduplicated by
    ``street_id``, and multiplies by ``pipe_capex_eur_per_km``.
    """
    seen: set = set()
    demand_length_m = 0.0
    for _, _, d in topology.edges(data=True):
        sid = d.get(street_id_column)
        if sid is not None and sid in seen:
            continue
        if sid is not None:
            seen.add(sid)
        if d.get(demand_column, 0.0) > 0.0:
            demand_length_m += float(d.get(street_length_column, 0.0))

    min_pipe_km = demand_length_m / 1000.0
    return {
        "min_pipe_km": float(min_pipe_km),
        "local_grid_capex_base_eur": float(pipe_capex_eur_per_km * min_pipe_km),
    }


def gdf_to_region_topologies(
    gdf: gpd.GeoDataFrame,
    key_column: str = "gemeindeschluessel",
) -> list[nx.Graph]:
    """Split a GeoDataFrame of line segments into one NetworkX graph per region.

    Groups rows by ``key_column`` and converts each group with :func:`gdf_to_nx`.
    The region key is stored in ``G.graph['id']`` for each graph.  Graphs are
    returned sorted by key value so that the ordering is deterministic.
    """
    topologies: list[nx.Graph] = []
    for key, group in gdf.groupby(key_column, sort=True):
        G = gdf_to_nx(group.copy())
        G.graph["id"] = key
        topologies.append(G)
    return topologies


def build_inter_dhn_pipes_from_topologies(
    *,
    regions: list[Region],
    full_network: nx.Graph,
    pipe_capex_eur_per_km: float,
    demand_column: str = "total_heat_demand",
    min_interdistrict_pipe_length_m: float = 1.0,
) -> dict[tuple[int, int], dict[str, float]]:
    """Compute inter-district pipe specs using shortest-path on the full street network.

    ``full_network`` must be a superset of all region topologies — it covers both
    the region streets and any connecting segments between regions.  For each pair
    of regions the function finds the shortest path (weighted by edge ``length``)
    from any demand node in region A to any demand node in region B.
    """
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

            reachable = {n: dist[n] for n in targets if n in dist}
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
