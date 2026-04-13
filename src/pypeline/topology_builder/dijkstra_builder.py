"""Dijkstra-driven topology builder orchestration.

Only owns end-to-end pipeline wiring for dataset-driven region topology generation.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import geopandas as gpd
import pandas as pd

from pypeline.topology_builder.region_topology_builder import (
    RegionTopologyConfig,
    build_region_topology,
)
from pypeline.plot.plotter import EnergySystemPlotter
from pypeline.topology_builder.core import (AbstractTopologyBuilder, TopologyBuildError, TopologyBuildResult, gdf_to_nx, gdf_to_region_topologies)


@dataclass(frozen=True)
class _OutputPaths:
    streets_out: Path
    topology_plot_out: Path


@dataclass(frozen=True)
class DijkstraTopologyBuilderConfig:
    input_dir: Path
    output_dir: Path
    buildings_file: str
    streets_file: str
    max_demand_mwh: float
    max_street_length_km: float
    demand_share_pct: float

    polynesia: bool = True
    city_column: str = "gemeindeschluessel"
    clip_buffer_m: float = 200.0
    connect_tolerance_m: float = 10.0
    small_islands: bool = True
    small_islands_max_segments: int = 60
    segment_streets_by_building_projections: bool = True
    segment_projection_buffer_m: float = 12.0
    region_id_column: str = "id"
    street_id_column: str = "street_id"
    building_id_column: str = "building_objectid"
    demand_building_column: str = "building_objectid"
    demand_source_column: str = "heating:demand[Wh]"
    demand_value_column: str = "annual_demand_mwh"
    demand_street_indicator_column: str = "total_heat_demand"
    demand_street_indicator_min: float = 0.0


def _compute_output_paths(output_dir: Path, dataset_name: str, *, polynesia: bool) -> _OutputPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    plots_dir = output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    plot_suffix = "_polynesia" if polynesia else ""
    return _OutputPaths(
        streets_out=output_dir / f"street_segments_{dataset_name}.geojson",
        topology_plot_out=plots_dir / f"district_topology_{dataset_name}{plot_suffix}.png",
    )


def _resolve_input_file(input_dir: Path, file_name_or_relpath: str) -> Path:
    rel = Path(file_name_or_relpath)
    if rel.is_absolute():
        return rel
    return input_dir / rel


def _run_dijkstra_pipeline(
    *,
    dataset_name: str,
    request: DijkstraTopologyBuilderConfig,
) -> TopologyBuildResult:
    buildings_path = _resolve_input_file(request.input_dir, request.buildings_file)
    streets_path = _resolve_input_file(request.input_dir, request.streets_file)

    if not buildings_path.exists():
        raise FileNotFoundError(f"Buildings file not found: {buildings_path}")
    if not streets_path.exists():
        raise FileNotFoundError(f"Streets file not found: {streets_path}")

    buildings = gpd.read_file(buildings_path)
    streets = gpd.read_file(streets_path)

    if request.building_id_column not in buildings.columns:
        raise ValueError(f"Missing building id column '{request.building_id_column}' in {buildings_path}")
    if request.demand_source_column not in buildings.columns:
        raise ValueError(f"Missing demand source column '{request.demand_source_column}' in {buildings_path}")

    demand_by_building = pd.DataFrame(
        {
            request.demand_building_column: buildings[request.building_id_column],
            request.demand_value_column: pd.to_numeric(
                buildings[request.demand_source_column], errors="coerce"
            ).fillna(0.0) / 1_000_000.0,
        }
    )

    city_value = None
    if (
        request.city_column
        and request.city_column in buildings.columns
        and request.city_column in streets.columns
    ):
        vals = buildings[request.city_column].dropna().astype(str)
        if vals.empty:
            raise ValueError(f"No values in city column '{request.city_column}'")
        city_value = str(vals.mode().iloc[0])

    topology_config = RegionTopologyConfig.from_required_caps(
        max_demand_mwh=request.max_demand_mwh,
        max_street_length_km=request.max_street_length_km,
        demand_share_pct=request.demand_share_pct,
        city_column=request.city_column,
        city_value=city_value,
        clip_buffer_m=request.clip_buffer_m,
        connect_tolerance_m=request.connect_tolerance_m,
        polynesia=request.polynesia,
        small_islands=request.small_islands,
        small_islands_max_segments=request.small_islands_max_segments,
        segment_streets_by_building_projections=request.segment_streets_by_building_projections,
        segment_projection_buffer_m=request.segment_projection_buffer_m,
        region_id_column=request.region_id_column,
        street_id_column=request.street_id_column,
        building_id_column=request.building_id_column,
        demand_building_column=request.demand_building_column,
        demand_value_column=request.demand_value_column,
        demand_street_indicator_column=request.demand_street_indicator_column,
        demand_street_indicator_min=request.demand_street_indicator_min,
    )

    streets_with_region, mantra = build_region_topology(
        buildings=buildings,
        streets=streets,
        demand_data=demand_by_building,
        config=topology_config,
    )

    artifacts = _compute_output_paths(request.output_dir, dataset_name, polynesia=request.polynesia)

    streets_with_region.to_file(artifacts.streets_out, driver="GeoJSON")

    EnergySystemPlotter.plot_streets_colored_by_region(
        streets_with_region=streets_with_region,
        output_path=artifacts.topology_plot_out,
        region_id_column=request.region_id_column,
        title=f"District topology: {dataset_name}",
        caps_legend={
            "max_demand_mwh": f"{request.max_demand_mwh:.0f}",
            "max_street_length_km": f"{request.max_street_length_km:.1f}",
            "demand_share_pct": f"{request.demand_share_pct:.0f}%",
        },
    )

    print(
        "check -> "
        f"raw street IDs split across regions: {mantra['raw_street_ids_split_across_regions']}, "
        f"unassigned street segments: {mantra['unassigned_street_segments']}, "
        f"cross-region crossings: {mantra['cross_region_crossings']}, "
        f"junction-overload points: {mantra['junction_overload_points']}, "
        f"disconnected regions: {mantra['disconnected_regions']}"
    )
    print(f"Wrote district street segments: {artifacts.streets_out} ({len(streets_with_region)} features)")
    print(f"Wrote topology plot:          {artifacts.topology_plot_out}")

    assigned = streets_with_region[streets_with_region[request.region_id_column].notna()].copy()
    if assigned.empty:
        raise ValueError("Missing region-assigned street segments")

    region_topologies = gdf_to_region_topologies(
        assigned,
        key_column=request.region_id_column,
    )
    street_network = gdf_to_nx(streets_with_region)

    return TopologyBuildResult(
        network=street_network,
        region_topologies=region_topologies,
        streets=streets_with_region,
    )


class DijkstraTopologyBuilder(AbstractTopologyBuilder):
    """Graph topology builder based on Dijkstra-driven region topology."""

    def __init__(self, config: DijkstraTopologyBuilderConfig):
        self.config = config

    @classmethod
    def from_config(cls, config: DijkstraTopologyBuilderConfig) -> "DijkstraTopologyBuilder":
        return cls(config)

    def build(self) -> TopologyBuildResult:
        dataset_name = self.config.input_dir.parent.name

        try:
            return _run_dijkstra_pipeline(
                dataset_name=dataset_name,
                request=self.config,
            )
        except Exception as exc:
            raise TopologyBuildError("Error when building topology.") from exc
