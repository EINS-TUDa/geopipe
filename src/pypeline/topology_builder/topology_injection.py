from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd
import yaml

from pypeline.injection import _apply_injections, _find_segment_indices
from pypeline.topology_builder.abstract_topology_builder import (
    AbstractTopologyBuilder,
    TopologyBuildResult,
)


def load_scenario_yaml(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        raise FileNotFoundError(f"Scenario file not found: {config_file}")
    with config_file.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping root in {config_file}, got {type(data).__name__}")
    return data


def _resolve_region_anchor_indices(
    streets: gpd.GeoDataFrame,
    *,
    region: dict[str, Any],
    region_idx: int,
    street_id_column: str,
) -> list[int]:
    segment_ids = region.get("street_segment_ids")
    if not isinstance(segment_ids, list) or not segment_ids:
        raise ValueError(f"regions[{region_idx}].street_segment_ids must be a non-empty list")

    indices: list[int] = []
    for segment_id in segment_ids:
        hit = _find_segment_indices(streets, street_id_column=street_id_column, segment_id=segment_id)
        if not hit:
            raise ValueError(f"Unknown street_segment_id '{segment_id}' in regions[{region_idx}]")
        indices.extend(hit)

    return sorted(set(int(i) for i in indices))


def _first_geometry_node(geometry) -> tuple[float, float]:
    if geometry is None:
        raise ValueError("Street segment geometry is missing")

    if geometry.geom_type == "LineString":
        lines = [geometry]
    elif geometry.geom_type == "MultiLineString":
        lines = list(geometry.geoms)
    else:
        raise ValueError(f"Unsupported street geometry type '{geometry.geom_type}'")

    if not lines:
        raise ValueError("Street segment has no line parts")

    coords = list(lines[0].coords)
    if not coords:
        raise ValueError("Street segment has no coordinates")

    return (float(coords[0][0]), float(coords[0][1]))


def _expand_region_with_shortest_paths(
    streets: gpd.GeoDataFrame,
    *,
    seed_indices: list[int],
    full_network: nx.Graph,
    street_id_column: str,
) -> list[int]:
    if len(seed_indices) <= 1:
        return seed_indices

    selected = set(int(i) for i in seed_indices)
    anchor_idx = int(seed_indices[0])
    anchor_node = _first_geometry_node(streets.at[anchor_idx, "geometry"])

    for row_idx in seed_indices[1:]:
        target_idx = int(row_idx)
        target_node = _first_geometry_node(streets.at[target_idx, "geometry"])
        try:
            path = nx.shortest_path(full_network, source=anchor_node, target=target_node, weight="length")
        except nx.NetworkXNoPath as exc:
            src_sid = streets.at[anchor_idx, street_id_column]
            dst_sid = streets.at[target_idx, street_id_column]
            raise ValueError(f"No path found to connect street segments '{src_sid}' and '{dst_sid}'") from exc

        for u, v in zip(path[:-1], path[1:]):
            edge_data = full_network.get_edge_data(u, v) or {}
            attr_dicts: list[dict[str, Any]]
            if isinstance(edge_data, dict) and street_id_column in edge_data:
                attr_dicts = [edge_data]
            elif isinstance(edge_data, dict):
                attr_dicts = [val for val in edge_data.values() if isinstance(val, dict)]
            else:
                attr_dicts = []

            for attrs in attr_dicts:
                sid = attrs.get(street_id_column)
                if sid is None:
                    continue
                selected.update(
                    _find_segment_indices(streets, street_id_column=street_id_column, segment_id=sid)
                )

    return sorted(selected)


def _assign_regions(
    streets: gpd.GeoDataFrame,
    *,
    regions: list[dict[str, Any]],
    region_id_column: str,
    street_id_column: str,
) -> gpd.GeoDataFrame:
    if not regions:
        raise ValueError("Scenario must define a non-empty 'regions' list")

    assigned = streets.copy()
    assigned[region_id_column] = pd.NA

    full_network: nx.Graph | None = None
    owner_by_row: dict[int, Any] = {}

    for region_idx, region in enumerate(regions):
        if not isinstance(region, dict):
            raise ValueError(f"regions[{region_idx}] must be a mapping")

        allowed_region_keys = {
            "region_id",
            "street_segment_ids",
        }
        unsupported_keys = sorted(set(region.keys()) - allowed_region_keys)
        if unsupported_keys:
            raise ValueError(f"regions[{region_idx}] has unsupported keys: {unsupported_keys}")

        region_id = region.get("region_id")
        if region_id is None:
            raise ValueError(f"regions[{region_idx}] missing 'region_id'")

        seed_indices = _resolve_region_anchor_indices(
            assigned,
            region=region,
            region_idx=region_idx,
            street_id_column=street_id_column,
        )

        resolved_indices = seed_indices
        if len(seed_indices) > 1:
            if full_network is None:
                full_network = AbstractTopologyBuilder.gdf_to_nx(assigned)
            resolved_indices = _expand_region_with_shortest_paths(
                assigned,
                seed_indices=seed_indices,
                full_network=full_network,
                street_id_column=street_id_column,
            )

        for row_idx in resolved_indices:
            previous_region = owner_by_row.get(int(row_idx))
            if previous_region is not None and previous_region != region_id:
                sid = assigned.at[int(row_idx), street_id_column]
                raise ValueError(
                    f"Street segment '{sid}' is assigned to multiple regions ({previous_region}, {region_id})"
                )
            owner_by_row[int(row_idx)] = region_id

    if not owner_by_row:
        raise ValueError("Scenario region mapping resulted in no assigned street segments")

    for row_idx, region_id in owner_by_row.items():
        assigned.at[int(row_idx), region_id_column] = region_id

    return assigned


def _run_simple_pipeline(
    *,
    streets_file: Path,
    scenario_file: Path,
    region_id_column: str,
    street_id_column: str,
    demand_column: str,
    street_length_column: str,
    apply_injections: bool,
) -> TopologyBuildResult:
    streets = gpd.read_file(streets_file)
    if street_length_column not in streets.columns:
        streets[street_length_column] = streets.geometry.length.astype(float)

    scenario_data = load_scenario_yaml(scenario_file)
    regions = scenario_data.get("regions", [])
    if not isinstance(regions, list):
        raise ValueError("Scenario 'regions' must be a list")

    injections = scenario_data.get("injections", [])
    if injections is None:
        injections = []
    if not isinstance(injections, list):
        raise ValueError("Scenario 'injections' must be a list")

    allowed_root_keys = {"regions", "injections"}
    unsupported_root_keys = sorted(set(scenario_data.keys()) - allowed_root_keys)
    if unsupported_root_keys:
        raise ValueError(f"Scenario has unsupported root keys: {unsupported_root_keys}")

    streets = _assign_regions(
        streets,
        regions=regions,
        region_id_column=region_id_column,
        street_id_column=street_id_column,
    )

    injected_demand_mwh = 0.0
    injected_techs: list[dict[str, Any]] = []
    if apply_injections:
        streets, injected_demand_mwh, injected_techs = _apply_injections(
            streets,
            injections=injections,
            street_id_column=street_id_column,
            demand_column=demand_column,
            region_id_column=region_id_column,
        )

    assigned = streets[streets[region_id_column].notna()].copy()
    if assigned.empty:
        raise ValueError("Missing region-assigned street segments")

    region_topologies = AbstractTopologyBuilder.gdf_to_region_topologies(
        assigned,
        key_column=region_id_column,
    )
    if not region_topologies:
        raise ValueError("No region topologies built")

    street_network = AbstractTopologyBuilder.gdf_to_nx(streets)
    return TopologyBuildResult(
        network=street_network,
        region_topologies=region_topologies,
        streets=streets,
        injected_demand_mwh=injected_demand_mwh,
        injected_techs=injected_techs,
    )


class SimpleTopologyBuilder(AbstractTopologyBuilder):
    """Build region topologies from case.yaml using street-segment IDs."""

    def __init__(
        self,
        *,
        streets_file: Path,
        scenario_file: Path,
        region_id_column: str = "id",
        street_id_column: str = "street_id",
        demand_column: str = "total_heat_demand",
        street_length_column: str = "street_length",
        apply_injections: bool = True,
    ) -> None:
        self.streets_file = streets_file
        self.scenario_file = scenario_file
        self.region_id_column = region_id_column
        self.street_id_column = street_id_column
        self.demand_column = demand_column
        self.street_length_column = street_length_column
        self.apply_injections = apply_injections

    def build(self) -> TopologyBuildResult:
        return _run_simple_pipeline(
            streets_file=self.streets_file,
            scenario_file=self.scenario_file,
            region_id_column=self.region_id_column,
            street_id_column=self.street_id_column,
            demand_column=self.demand_column,
            street_length_column=self.street_length_column,
            apply_injections=self.apply_injections,
        )
