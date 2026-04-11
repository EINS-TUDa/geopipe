from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING

import geopandas as gpd  # used by gdf_to_nx and gdf_to_region_topologies
import networkx as nx
import pandas as pd
from pypeline.topology_builder.abstract_topology_builder import AbstractTopologyBuilder

if TYPE_CHECKING:
    from pypeline.energy_system.region import Region


@dataclass(frozen=True)
class _RegionTopologyProxy:
    id: int
    topology: nx.Graph


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
    return AbstractTopologyBuilder.gdf_to_nx(gdf)


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
    return AbstractTopologyBuilder.gdf_to_region_topologies(gdf, key_column=key_column)


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


def _ensure_street_length_column(
    street_segments_gdf: gpd.GeoDataFrame,
    *,
    street_length_column: str,
) -> gpd.GeoDataFrame:
    segments = street_segments_gdf.copy()
    if street_length_column not in segments.columns:
        segments[street_length_column] = segments.geometry.length.astype(float)
    else:
        segments[street_length_column] = pd.to_numeric(
            segments[street_length_column], errors="coerce"
        ).fillna(segments.geometry.length.astype(float))
    return segments


def _resolve_demand_column(
    street_segments_gdf: gpd.GeoDataFrame,
    demand_column: str | None,
) -> str:
    if demand_column is not None:
        if demand_column not in street_segments_gdf.columns:
            raise ValueError(f"Missing demand column '{demand_column}' in street segments")
        return demand_column
    if "total_heat_demand" in street_segments_gdf.columns:
        return "total_heat_demand"
    if "_is_demand_street" in street_segments_gdf.columns:
        return "_is_demand_street"
    raise ValueError("Could not infer demand column; expected 'total_heat_demand' or '_is_demand_street'")


def calculate_district_heat_grid_cost(
    *,
    polygons: gpd.GeoDataFrame,
    district_id: int,
    local_pipe_capex_eur_per_km: float,
    street_segments_gdf: gpd.GeoDataFrame,
    region_id_column: str = "id",
    street_length_column: str = "street_length",
    street_id_column: str = "street_id",
    demand_column: str | None = None,
) -> dict[str, float]:
    """Compatibility helper: compute local DHN cost for a district from street segments."""
    if region_id_column not in polygons.columns:
        raise ValueError(f"Missing region id column '{region_id_column}' in polygons")
    if region_id_column not in street_segments_gdf.columns:
        raise ValueError(f"Missing region id column '{region_id_column}' in street segments")

    segments = _ensure_street_length_column(
        street_segments_gdf,
        street_length_column=street_length_column,
    )
    resolved_demand_column = _resolve_demand_column(segments, demand_column)

    if resolved_demand_column != "_is_demand_street":
        segments[resolved_demand_column] = pd.to_numeric(
            segments[resolved_demand_column], errors="coerce"
        ).fillna(0.0)

    mask = segments[region_id_column] == district_id
    if not bool(mask.any()):
        mask = segments[region_id_column].astype(str) == str(district_id)
    district_segments = segments[mask].copy()
    if district_segments.empty:
        raise ValueError(f"No street segments found for district '{district_id}'")

    topology = gdf_to_nx(district_segments)
    return build_district_heat_grid_from_topology(
        topology=topology,
        pipe_capex_eur_per_km=local_pipe_capex_eur_per_km,
        demand_column=resolved_demand_column,
        street_length_column=street_length_column,
        street_id_column=street_id_column,
    )


def build_district_heat_grid_from_polygons(
    *,
    polygons: gpd.GeoDataFrame,
    local_pipe_capex_eur_per_km: float,
    street_segments_gdf: gpd.GeoDataFrame,
    region_id_column: str = "id",
    street_length_column: str = "street_length",
    street_id_column: str = "street_id",
    demand_column: str | None = None,
) -> dict[int, dict[str, float]]:
    """Compatibility helper: compute local DHN costs for all polygon districts."""
    if region_id_column not in polygons.columns:
        raise ValueError(f"Missing region id column '{region_id_column}' in polygons")

    payload: dict[int, dict[str, float]] = {}
    district_ids = sorted({int(v) for v in polygons[region_id_column].dropna().tolist()})
    for district_id in district_ids:
        try:
            result = calculate_district_heat_grid_cost(
                polygons=polygons,
                district_id=district_id,
                local_pipe_capex_eur_per_km=local_pipe_capex_eur_per_km,
                street_segments_gdf=street_segments_gdf,
                region_id_column=region_id_column,
                street_length_column=street_length_column,
                street_id_column=street_id_column,
                demand_column=demand_column,
            )
            payload[district_id] = {
                "min_pipe_km": float(result.get("min_pipe_km", 0.0)),
                "local_grid_capex_base_eur": 0.0,
            }
        except ValueError:
            payload[district_id] = {
                "min_pipe_km": 0.0,
                "local_grid_capex_base_eur": 0.0,
            }
    return payload


def build_inter_dhn_pipes_from_street_segments(
    *,
    polygons: gpd.GeoDataFrame,
    street_segments_gdf: gpd.GeoDataFrame,
    pipe_capex_eur_per_km: float,
    region_id_column: str = "id",
    street_length_column: str = "street_length",
    demand_column: str | None = None,
    min_interdistrict_pipe_length_m: float = 1.0,
) -> dict[tuple[int, int], dict[str, float]]:
    """Compatibility helper: compute inter-district pipes directly from street segments."""
    if region_id_column not in polygons.columns:
        raise ValueError(f"Missing region id column '{region_id_column}' in polygons")
    if region_id_column not in street_segments_gdf.columns:
        raise ValueError(f"Missing region id column '{region_id_column}' in street segments")

    segments = _ensure_street_length_column(
        street_segments_gdf,
        street_length_column=street_length_column,
    )
    resolved_demand_column = _resolve_demand_column(segments, demand_column)

    if resolved_demand_column != "_is_demand_street":
        segments[resolved_demand_column] = pd.to_numeric(
            segments[resolved_demand_column], errors="coerce"
        ).fillna(0.0)

    assigned = segments[segments[region_id_column].notna()].copy()
    if assigned.empty:
        return {}

    region_topologies = gdf_to_region_topologies(assigned, key_column=region_id_column)
    topology_by_id = {int(graph.graph["id"]): graph for graph in region_topologies}

    ordered_ids = sorted({int(v) for v in polygons[region_id_column].dropna().tolist()})
    regions: list[_RegionTopologyProxy] = []
    for district_id in ordered_ids:
        topology = topology_by_id.get(district_id)
        if topology is not None:
            regions.append(_RegionTopologyProxy(id=district_id, topology=topology))

    if len(regions) < 2:
        return {}

    full_network = gdf_to_nx(segments)
    return build_inter_dhn_pipes_from_topologies(
        regions=regions,
        full_network=full_network,
        pipe_capex_eur_per_km=pipe_capex_eur_per_km,
        demand_column=resolved_demand_column,
        min_interdistrict_pipe_length_m=min_interdistrict_pipe_length_m,
    )
