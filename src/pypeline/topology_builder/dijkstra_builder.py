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
    segment_streets_by_building_projections: bool = False
    segment_projection_buffer_m: float = 12.0
    region_id_column: str = "id"
    street_id_column: str = "street_id"
    building_id_column: str = "building_objectid"
    demand_building_column: str = "building_objectid"
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


def _normalize_streets_for_topology(
    streets: gpd.GeoDataFrame,
    *,
    street_id_column: str,
    demand_street_indicator_column: str,
) -> gpd.GeoDataFrame:
    s = streets.copy()

    if s.empty:
        raise ValueError("Streets file is empty")

    geom_types = set(s.geometry.geom_type.dropna().astype(str).tolist())
    lineal_types = {"LineString", "MultiLineString"}

    if not geom_types.issubset(lineal_types):
        non_line_mask = ~s.geometry.geom_type.isin(list(lineal_types))
        if bool(non_line_mask.any()):
            s.loc[non_line_mask, "geometry"] = s.loc[non_line_mask, "geometry"].boundary

    s = s.explode(index_parts=False).reset_index(drop=True)
    s = s[s.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
    if s.empty:
        raise ValueError("No line-like street geometries after normalization")

    if demand_street_indicator_column not in s.columns:
        for candidate in (
            "total_heat_demand",
            "qnutzwaerme_2020_kwh",
            "raumwaerme",
            "heating:demand[Wh]",
            "heat_demand",
        ):
            if candidate in s.columns:
                s[demand_street_indicator_column] = pd.to_numeric(s[candidate], errors="coerce").fillna(0.0)
                break

    if (
        street_id_column not in s.columns
        or s[street_id_column].isna().any()
        or not s[street_id_column].is_unique
    ):
        s[street_id_column] = range(len(s))

    return s


def _buildings_and_demand_from_streets(
    streets: gpd.GeoDataFrame,
    *,
    city_column: str,
    building_id_column: str,
    demand_building_column: str,
    demand_street_indicator_column: str,
    demand_value_column: str,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    if demand_street_indicator_column not in streets.columns:
        raise ValueError(
            f"Missing demand street indicator column '{demand_street_indicator_column}' in streets file"
        )

    demand_mwh = pd.to_numeric(
        streets[demand_street_indicator_column], errors="coerce"
    ).fillna(0.0) / 1_000_000.0

    pseudo_ids = pd.Series(range(len(streets)), dtype="int64")
    geometry = streets.geometry.representative_point().reset_index(drop=True)

    data: dict[str, pd.Series] = {
        building_id_column: pseudo_ids,
    }
    if city_column and city_column in streets.columns:
        data[city_column] = streets[city_column].reset_index(drop=True)

    pseudo_buildings = gpd.GeoDataFrame(data=data, geometry=geometry, crs=streets.crs)
    demand_by_building = pd.DataFrame(
        {
            demand_building_column: pseudo_buildings[building_id_column],
            demand_value_column: demand_mwh.reset_index(drop=True),
        }
    )

    return pseudo_buildings, demand_by_building


def _run_dijkstra_pipeline(
    *,
    dataset_name: str,
    request: DijkstraTopologyBuilderConfig,
) -> TopologyBuildResult:
    streets_path = _resolve_input_file(request.input_dir, request.streets_file)

    if not streets_path.exists():
        raise FileNotFoundError(f"Streets file not found: {streets_path}")

    streets = _normalize_streets_for_topology(
        gpd.read_file(streets_path),
        street_id_column=request.street_id_column,
        demand_street_indicator_column=request.demand_street_indicator_column,
    )

    buildings, demand_by_building = _buildings_and_demand_from_streets(
        streets,
        city_column=request.city_column,
        building_id_column=request.building_id_column,
        demand_building_column=request.demand_building_column,
        demand_street_indicator_column=request.demand_street_indicator_column,
        demand_value_column=request.demand_value_column,
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
