"""Cross-region connectivity resolution (migration step b — skeleton).

Computes existing capacity for grids, central technologies, and inter-region
pipes from the per-region decentralized capacity already produced by
``RegionResolver``. Generalizes the old DH-specific logic in
``builder.EnergySystemBuilder`` and ``energy_system.dhn`` to any grid type
declared in ``UserConfig.grids``.

Algorithm (per ``grid_name`` in ``user_config.grids``):

1. Identify eligible regions: those whose decentralized techs have
   ``commodity_in`` == this grid's ``commodity_out``, with effective share
   strictly above ``minimum_dhn_share``.
2. Form connected groups: pairwise distance below
   ``considered_connected_distance_m`` joins regions into the same group.
3. Per group, pick the central region:
   - If exactly one ``preferred_central_location_region_id`` falls in the
     group, that wins.
   - Else: the region with the highest annual demand for the
     grid-fed commodity.
4. Compute shortest-path flows from each non-central group member to the
   central region through ``region_network``. Per-edge flow is the sum of
   downstream demand crossing that edge.
5. Derive capacities under chained losses
   (grid_eff × pipe_eff ^ remaining_hops) and apply
   ``(1 + grid_expansion_headroom_pct)`` headroom.
6. Instantiate ``Grid`` per region, ``CentralTech`` at the central region,
   ``Pipe`` per spanning-tree edge.

NOTE: This is a skeleton — most helpers are stubs. The pieces marked
``NotImplementedError`` are the real work of step b2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import networkx as nx

from pypeline.energy_technology.technology_new import (
    CentralTech,
    CentralTechType,
    CHP,
    CHPType,
    DecentralTech,
    Grid,
    GridType,
    Pipe,
    PipeType,
)
from pypeline.energy_technology.technology_registry_new import TechnologyRegistry
from pypeline.user_config import GridConfig, UserConfig

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegionState:
    """Minimal per-region input the resolver needs.

    Decoupled from ``energy_system.core.Region`` so the resolver doesn't
    know about street networks, rule books, etc. The new ``Region`` shape
    (post step b3) will be trivially convertible to this.
    """

    region_id: int
    decentralized: list[DecentralTech]
    annual_demand_by_commodity: dict[str, float]
    centroid: "BaseGeometry"


@dataclass
class ConnectivityResult:
    """Output of ``resolve_connectivity``."""

    grids_by_region: dict[int, list[Grid]] = field(default_factory=dict)
    central_by_region: dict[int, list[CentralTech | CHP]] = field(default_factory=dict)
    pipes: list[Pipe] = field(default_factory=list)
    connected_groups: dict[str, list[frozenset[int]]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def resolve_connectivity(
    regions: list[RegionState],
    registry: TechnologyRegistry,
    user_config: UserConfig,
    *,
    region_network: nx.Graph,
    pipe_technology_name: str,
) -> ConnectivityResult:
    """Resolve grids / central capacity / pipes across regions.

    ``region_network`` is an undirected graph whose nodes are region ids
    and edge attribute ``length_m`` holds inter-region distance. (Caller
    builds this from the street network — see ``_build_region_network``
    in step b2 implementation.)
    """
    result = ConnectivityResult()
    region_index = {r.region_id: r for r in regions}
    pipe_type = registry.pipes[pipe_technology_name]

    for grid_name, grid_cfg in user_config.grids.items():
        grid_type = registry.grids[grid_name]
        eligible_ids = _eligible_regions_for_grid(regions, grid_type, grid_cfg)
        if not eligible_ids:
            continue

        groups = _compute_connected_groups(
            eligible_ids,
            region_index,
            grid_cfg.considered_connected_distance_m,
        )
        result.connected_groups[grid_name] = groups

        for group in groups:
            central_id = _pick_central_region(group, grid_cfg, region_index, grid_type)

            flows = _compute_flows(
                group=group,
                central_id=central_id,
                region_index=region_index,
                region_network=region_network,
                grid_type=grid_type,
                pipe_type=pipe_type,
            )

            _emit_central(
                central_id=central_id,
                flows=flows,
                grid_cfg=grid_cfg,
                grid_type=grid_type,
                registry=registry,
                result=result,
            )
            _emit_grids(
                group=group,
                flows=flows,
                grid_cfg=grid_cfg,
                grid_type=grid_type,
                result=result,
            )
            _emit_pipes(
                flows=flows,
                grid_cfg=grid_cfg,
                grid_type=grid_type,
                registry=registry,
                pipe_technology_name=pipe_technology_name,
                region_network=region_network,
                result=result,
            )

    return result


# ---------------------------------------------------------------------------
# Step 1: eligibility
# ---------------------------------------------------------------------------


def _eligible_regions_for_grid(
    regions: list[RegionState],
    grid_type: GridType,
    grid_cfg: GridConfig,
) -> set[int]:
    """Return ids of regions with non-trivial decentral demand on this grid.

    A region is eligible iff one of its decentralized techs has
    ``commodity_in == grid_type.commodity_out`` AND the *effective* share
    of that tech (after applying ``minimum_dhn_share_for(region)``) is
    strictly positive. The minimum-share threshold is applied upstream by
    the RegionResolver — here we trust ``existing_capacity`` and check
    that it's > 0.
    """
    eligible: set[int] = set()
    for region in regions:
        for tech in region.decentralized:
            if tech.commodity_in != grid_type.commodity_out:
                continue
            if tech.existing_capacity <= 0:
                continue
            eligible.add(region.region_id)
            break
    return eligible


# ---------------------------------------------------------------------------
# Step 2: connected groups
# ---------------------------------------------------------------------------


def _compute_connected_groups(
    eligible_ids: set[int],
    region_index: dict[int, RegionState],
    distance_threshold_m: float,
) -> list[frozenset[int]]:
    """Group eligible regions whose centroids are within the threshold.

    Builds a graph on eligible regions, edges where distance ≤ threshold,
    returns connected components. ``threshold == 0`` => every region is
    its own group.
    """
    g = nx.Graph()
    g.add_nodes_from(eligible_ids)
    if distance_threshold_m > 0:
        ids = sorted(eligible_ids)
        for i, a in enumerate(ids):
            ca = region_index[a].centroid
            for b in ids[i + 1 :]:
                cb = region_index[b].centroid
                if ca.distance(cb) <= distance_threshold_m:
                    g.add_edge(a, b)
    return [frozenset(comp) for comp in nx.connected_components(g)]


# ---------------------------------------------------------------------------
# Step 3: pick central
# ---------------------------------------------------------------------------


def _pick_central_region(
    group: frozenset[int],
    grid_cfg: GridConfig,
    region_index: dict[int, RegionState],
    grid_type: GridType,
) -> int:
    """Preferred-location wins; otherwise the region with the highest
    annual demand on the grid-fed commodity.
    """
    preferred = grid_cfg.preferred_location_in_group(set(group))
    if preferred is not None:
        return preferred

    fed_commodity = grid_type.commodity_out  # what decentral techs consume
    return max(
        group,
        key=lambda rid: region_index[rid].annual_demand_by_commodity.get(fed_commodity, 0.0),
    )


# ---------------------------------------------------------------------------
# Step 4: flow computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlowRecord:
    """Per-region grid-input demand and path back to central.

    ``grid_input_mw`` is the peak MW the grid must deliver at this region's
    grid input (i.e. ``commodity_in`` of the grid). It already includes the
    grid efficiency factor — see ``_compute_flows``. Pipe-loss inflation
    is applied per-edge, not stored here.
    """

    region_id: int
    grid_input_mw: float
    hops_to_central: int
    path_to_central: tuple[int, ...]


@dataclass(frozen=True)
class FlowGraph:
    """Output of ``_compute_flows``.

    ``edge_flows`` keys are *directed* ``(upstream, downstream)`` tuples
    where ``upstream`` is the node closer to ``central_id`` along the
    shortest-path tree. The MW value is the flow at the upstream side of
    the pipe (= the size the pipe must support).
    """

    central_id: int
    records: dict[int, FlowRecord]
    edge_flows: dict[tuple[int, int], float]
    edge_lengths_m: dict[tuple[int, int], float]


def _compute_flows(
    *,
    group: frozenset[int],
    central_id: int,
    region_index: dict[int, RegionState],
    region_network: nx.Graph,
    grid_type: GridType,
    pipe_type: PipeType,
) -> FlowGraph:
    """Route each region's grid-input demand back to ``central_id``.

    Per-region demand at the grid input:

        grid_input_mw[r] = sum(dt.existing_capacity for dt in r.decentralized
                               if dt.commodity_in == grid_type.commodity_out)
                           / grid_type.efficiency

    Pipe-loss compounding along a path of length ``k_r`` from central to
    region ``r``: the j-th edge (1-indexed) carries

        flow_for_r_at_edge_j = grid_input_mw[r] / pipe_eff ** (k_r - j + 1)

    Edge flows accumulate across regions sharing edges. Coincident peaks
    are assumed (cooperation_of_technologies => same profile across
    decentral techs supplying the same demand, so summed peaks are tight).
    """
    pipe_eff = 1.0 - pipe_type.loss_percent
    grid_eff = grid_type.efficiency

    subgraph = region_network.subgraph(group)
    if not nx.is_connected(subgraph):
        raise ValueError(
            f"region_network is not connected over group {sorted(group)} for "
            f"grid '{grid_type.name}'. Caller must ensure regions in the same "
            f"connected group also share a path in region_network."
        )

    grid_input_mw: dict[int, float] = {}
    for rid in group:
        region = region_index[rid]
        hx_capacity = sum(
            dt.existing_capacity
            for dt in region.decentralized
            if dt.commodity_in == grid_type.commodity_out and dt.existing_capacity > 0
        )
        grid_input_mw[rid] = hx_capacity / grid_eff

    records: dict[int, FlowRecord] = {}
    edge_flows: dict[tuple[int, int], float] = {}
    edge_lengths_m: dict[tuple[int, int], float] = {}

    # Single-source shortest paths give a consistent tree rooted at central.
    paths = nx.single_source_dijkstra_path(subgraph, central_id, weight="length_m")

    for rid in group:
        path = tuple(paths[rid])
        k_r = len(path) - 1
        records[rid] = FlowRecord(
            region_id=rid,
            grid_input_mw=grid_input_mw[rid],
            hops_to_central=k_r,
            path_to_central=path,
        )
        if k_r == 0:
            continue

        Y_r = grid_input_mw[rid]
        for j, (upstream, downstream) in enumerate(zip(path[:-1], path[1:]), start=1):
            edge_key = (upstream, downstream)
            contribution = Y_r / (pipe_eff ** (k_r - j + 1))
            edge_flows[edge_key] = edge_flows.get(edge_key, 0.0) + contribution
            if edge_key not in edge_lengths_m:
                edge_lengths_m[edge_key] = float(subgraph.edges[upstream, downstream]["length_m"])

    return FlowGraph(
        central_id=central_id,
        records=records,
        edge_flows=edge_flows,
        edge_lengths_m=edge_lengths_m,
    )


# ---------------------------------------------------------------------------
# Step 5–6: capacity derivation & instantiation
# ---------------------------------------------------------------------------


def _emit_central(
    *,
    central_id: int,
    flows: FlowGraph,
    grid_cfg: GridConfig,
    grid_type: GridType,
    registry: TechnologyRegistry,
    result: ConnectivityResult,
) -> None:
    """Instantiate the central tech at ``central_id``.

    Central output (district_heat_in MW) = own grid_input + flow leaving
    central via outgoing pipes. Equivalently:
    ``sum(grid_input_mw[r] / pipe_eff^hops[r] for r in group)``. Both
    forms agree by edge-flow conservation (verified in tests).

    Headroom is *not* applied to central capacity — headroom is reserved
    for grid+pipes expansion; growing supply requires new central capex.
    """
    if grid_cfg.default_central_technology is None:
        raise ValueError(
            f"grid '{grid_type.name}' has eligible regions but no "
            f"default_central_technology in user config"
        )

    central_output_mw = flows.records[central_id].grid_input_mw
    for (upstream, _), flow_mw in flows.edge_flows.items():
        if upstream == central_id:
            central_output_mw += flow_mw

    central_type = registry[grid_cfg.default_central_technology]
    if isinstance(central_type, CentralTechType) and central_type.cap_max is not None:
        max_total = central_type.cap_max * (central_type.max_units or 1)
        if central_output_mw > max_total + 1e-9:
            raise ValueError(
                f"Computed existing central capacity {central_output_mw:.4f} MW for "
                f"'{central_type.name}' at region {central_id} exceeds "
                f"cap_max ({central_type.cap_max}) * max_units "
                f"({central_type.max_units or 1}) = {max_total}"
            )
    if not isinstance(central_type, (CentralTechType, CHPType)):
        raise TypeError(
            f"default_central_technology '{central_type.name}' must be a CentralTechType "
            f"or CHPType (got {type(central_type).__name__}) — should have been caught by "
            f"UserConfig.validate_against_registry"
        )

    instance = central_type.instantiate(existing_capacity=central_output_mw)
    result.central_by_region.setdefault(central_id, []).append(instance)


def _emit_grids(
    *,
    group: frozenset[int],
    flows: FlowGraph,
    grid_cfg: GridConfig,
    grid_type: GridType,
    result: ConnectivityResult,
) -> None:
    """Instantiate one ``Grid`` per region in ``group``.

    Throughput at region r (district_heat_in MW) = own grid_input + sum of
    outgoing edge flows (pass-through to downstream regions). By edge-flow
    conservation this equals the inflow from upstream pipes after pipe
    loss, so it sizes the grid to the actual MW it must handle.
    """
    headroom = 1.0 + grid_cfg.grid_expansion_headroom_pct
    for rid in group:
        throughput_mw = flows.records[rid].grid_input_mw
        for (upstream, _), flow_mw in flows.edge_flows.items():
            if upstream == rid:
                throughput_mw += flow_mw
        instance = grid_type.instantiate(existing_capacity=throughput_mw * headroom)
        result.grids_by_region.setdefault(rid, []).append(instance)


def _emit_pipes(
    *,
    flows: FlowGraph,
    grid_cfg: GridConfig,
    grid_type: GridType,
    registry: TechnologyRegistry,
    pipe_technology_name: str,
    region_network: nx.Graph,
    result: ConnectivityResult,
) -> None:
    """Instantiate one ``Pipe`` per edge in the group's shortest-path tree.

    Pipe existing capacity (MW) = ``flows.edge_flows[edge]`` × headroom.
    Direction: ``region_id_in`` is the upstream (closer-to-central) node,
    matching the ``district_heat_out → district_heat_in`` convention.
    ``below_distance_threshold`` is set when ``length_m`` is at or below
    ``considered_connected_distance_m`` — those pipes are treated as
    pre-existing and pay no capex (per ``Pipe.__post_init__``).
    """
    headroom = 1.0 + grid_cfg.grid_expansion_headroom_pct
    threshold_m = grid_cfg.considered_connected_distance_m
    pipe_type = registry.pipes[pipe_technology_name]

    for (upstream, downstream), flow_mw in flows.edge_flows.items():
        length_m = flows.edge_lengths_m[(upstream, downstream)]
        below = threshold_m > 0 and length_m <= threshold_m
        length_km = length_m / 1000.0
        pipe = pipe_type.instantiate(
            region_id_in=upstream,
            region_id_out=downstream,
            pipe_length_km=length_km,
            existing_capacity=flow_mw * headroom,
            below_distance_threshold=below,
            costs_eur=pipe_type.capex_per_km * length_km,
        )
        result.pipes.append(pipe)


__all__ = [
    "RegionState",
    "ConnectivityResult",
    "FlowRecord",
    "FlowGraph",
    "resolve_connectivity",
]
