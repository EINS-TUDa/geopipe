from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

from pypeline.energy_system.region_topology import (
    RegionCaps,
    RegionTopologyConfig,
    RegionTopologyEngine,
)
from pypeline.plot.plotter import EnergySystemPlotter
from pypeline.polygon_builder.polygon_builder import (
    AbstractPolygonBuilder,
    PolygonBuildError,
    PolygonBuildResult,
    PolygonBuilderConfig,
)

"""
This module prepares the spatial topology, later consumed by the optimizer. 

Pipeline: data -> street graph -> district polygons -> hand-off to optimizer.

1) Input data prep:
     - Convert building heat demand into annual MWh values.
     - Input selection is explicit (files + caps are passed by the caller).

    Input Data requierements:
    Assumptions:
        - Files use a compatible projected CRS (meter-based distance logic)
        - Buildings file (point geometry):
            - a stable building identifier (building_objectid by default)
            - annual heat demand source (heating:demand[Wh] by default)
            - optional city filter column (gemeindeschluessel by default)
        - Streets file (line geometry):
            - street line geometry that can be intersected/split into a graph
            - optional city filter column matching buildings
            - optional street demand indicator (total_heat_demand by default)

2) Street graph construction and district assignment (region_topology.topology_builder):
         - Convert the line network into a graph representation where street
             segments are nodes and geometric intersections define connectivity.
         - Propagate district ownership over that graph while respecting planning constraints.
         - When a region is disconnected or undersized, the algorithm searches for
             feasible connector paths. 
             The path search follows Dijkstra cost expansion so the merge/splits are topologically valid and distance-aware.
         - The output of this stage is an assigned street-segment graph (streets_with_region)

3) Polygon construction from assigned graph (region_topology.polygon_builder):
         - Use the previously generated graphs as the boundary signal for polygon synthesis.
         - Then runs a custom endpoint/dot-connection enclosure algo: 
            Classify open endpoints, propose legal connectors, score candidates by enclosed-area gain
            and iteratively close district hulls
         - Emit district polygons from the final hulls and align them with district IDs from the street graph

Files generated:
    - street_segments_{dataset}.geojson: street segments with assigned region IDs
    - polygon_{dataset}.geojson: district polygons with region IDs
    - plots/district_topology_{dataset}.png: street graph colored by assigned region IDs
    - plots/district_polygons_{dataset}.png: district polygons colored by region IDs

Shortest path algo reference:
- E. W. Dijkstra, “A Note on Two Problems in Connexion with Graphs,”
    Numerische Mathematik 1 (1959), pp. 269–271.
"""

# ---------------------------------------------------------------------------
# Dijkstra-specific configuration
# ---------------------------------------------------------------------------

@dataclass
class DijkstraPolygonBuilderConfig(PolygonBuilderConfig):
    """
    Inherits input_dir and output_dir from PolygonBuilderConfig.
    All Dijkstra-specific fields have sensible defaults so that only the
    required algorithmic parameters (buildings_file, streets_file, and the
    three capacity knobs) must be supplied explicitly.
    """
    # --- required ---
    buildings_file: str = ""
    streets_file: str = ""
    max_demand_mwh: float = 0.0
    max_street_length_km: float = 0.0
    demand_share_pct: float = 0.0

    # --- optional: algorithm tuning ---
    polynesia: bool = True
    city_column: str = "gemeindeschluessel"
    clip_buffer_m: float = 200.0
    connect_tolerance_m: float = 10.0
    small_islands: bool = True
    small_islands_max_segments: int = 60
    segment_streets_by_building_projections: bool = True
    segment_projection_buffer_m: float = 12.0

    # --- optional: column names ---
    region_id_column: str = "id"
    street_id_column: str = "street_id"
    building_id_column: str = "building_objectid"
    demand_building_column: str = "building_objectid"
    demand_source_column: str = "heating:demand[Wh]"
    demand_value_column: str = "annual_demand_mwh"
    demand_street_indicator_column: str = "total_heat_demand"
    demand_street_indicator_min: float = 0.0


# ---------------------------------------------------------------------------
# Output path helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _OutputPaths:
    polygons_out: Path
    streets_out: Path
    topology_plot_out: Path
    polygon_plot_out: Path


def _compute_output_paths(output_dir: Path, dataset_name: str, *, polynesia: bool) -> _OutputPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    plots_dir = output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    plot_suffix = "_polynesia" if polynesia else ""
    return _OutputPaths(
        polygons_out=output_dir / f"polygon_{dataset_name}.geojson",
        streets_out=output_dir / f"street_segments_{dataset_name}.geojson",
        topology_plot_out=plots_dir / f"district_topology_{dataset_name}{plot_suffix}.png",
        polygon_plot_out=plots_dir / f"district_polygons_{dataset_name}{plot_suffix}.png",
    )


# ---------------------------------------------------------------------------
# Core build pipeline
# ---------------------------------------------------------------------------

def _resolve_input_file(input_dir: Path, file_name_or_relpath: str) -> Path:
    rel = Path(file_name_or_relpath)
    if rel.is_absolute():
        return rel
    candidate = input_dir / rel
    if candidate.exists():
        return candidate
    # Legacy fallback: "input data" subfolder with a space
    legacy = input_dir / "input data" / rel
    if legacy.exists():
        return legacy
    return candidate


def _plot_polygons(polygons: gpd.GeoDataFrame, *, region_id_column: str, title: str, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 12))
    polygons.plot(ax=ax, alpha=0.35, edgecolor="black", linewidth=0.8)
    if region_id_column in polygons.columns:
        for _, row in polygons[[region_id_column, "geometry"]].iterrows():
            geom = row["geometry"]
            if geom is None or geom.is_empty:
                continue
            rp = geom.representative_point()
            ax.text(float(rp.x), float(rp.y), str(int(row[region_id_column])), ha="center", va="center", fontsize=8)
    ax.set_title(title)
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    fig.savefig(output_path, dpi=300)
    plt.close(fig)


def _run_dijkstra_pipeline(
    input_dir: Path,
    output_dir: Path,
    dataset_name: str,
    request: DijkstraPolygonBuilderConfig,
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Execute the full Dijkstra topology pipeline and write all output artifacts.
    Returns (polygons, district_street_segments) as GeoDataFrames.
    """
    buildings_path = _resolve_input_file(input_dir, request.buildings_file)
    streets_path = _resolve_input_file(input_dir, request.streets_file)

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

    b_for_algo = buildings.copy()
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
        b_for_algo = buildings[buildings[request.city_column].astype(str) == city_value].copy()

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

    _, streets_with_region, mantra = RegionTopologyEngine.build(
        buildings=buildings,
        streets=streets,
        demand_data=demand_by_building,
        config=topology_config,
    )

    polygons = RegionTopologyEngine.build_polygons_from_assigned_streets(
        buildings=b_for_algo,
        assigned_streets=streets_with_region,
        demand_data=demand_by_building,
        caps=RegionCaps(
            max_demand_mwh=request.max_demand_mwh,
            max_street_length_km=request.max_street_length_km,
            max_nondemand_street_km=None,
        ),
        config=topology_config,
    )

    artifacts = _compute_output_paths(output_dir, dataset_name, polynesia=request.polynesia)

    polygons.drop(columns=["_street_members", "_demand_street_members"], errors="ignore").to_file(
        artifacts.polygons_out, driver="GeoJSON"
    )
    streets_with_region.to_file(artifacts.streets_out, driver="GeoJSON")

    EnergySystemPlotter.plot_streets_colored_by_region(
        streets_with_region=streets_with_region,
        polygons=polygons,
        output_path=artifacts.topology_plot_out,
        region_id_column=request.region_id_column,
        title=f"District topology: {dataset_name}",
        caps_legend={
            "max_demand_mwh": f"{request.max_demand_mwh:.0f}",
            "max_street_length_km": f"{request.max_street_length_km:.1f}",
            "demand_share_pct": f"{request.demand_share_pct:.0f}%",
        },
    )

    _plot_polygons(
        polygons,
        region_id_column=request.region_id_column,
        title=f"District polygons: {dataset_name}",
        output_path=artifacts.polygon_plot_out,
    )

    print(
        "check -> "
        f"raw street IDs split across regions: {mantra['raw_street_ids_split_across_regions']}, "
        f"unassigned street segments: {mantra['unassigned_street_segments']}, "
        f"cross-region crossings: {mantra['cross_region_crossings']}, "
        f"junction-overload points: {mantra['junction_overload_points']}, "
        f"disconnected regions: {mantra['disconnected_regions']}"
    )
    print(f"Wrote polygons:               {artifacts.polygons_out} ({len(polygons)} region features)")
    print(f"Wrote district street segments: {artifacts.streets_out} ({len(streets_with_region)} features)")
    print(f"Wrote topology plot:          {artifacts.topology_plot_out}")
    print(f"Wrote polygon plot:           {artifacts.polygon_plot_out}")

    return polygons, streets_with_region


# ---------------------------------------------------------------------------
# Public builder class
# ---------------------------------------------------------------------------

class DijkstraPolygonBuilder(AbstractPolygonBuilder):
    """
    Polygon builder based on Dijkstra-driven region topology.

    Usage::

        cfg = DijkstraPolygonBuilderConfig(
            input_dir=Path("neuburg/input_data"),
            output_dir=Path("neuburg/output"),
            buildings_file="buildings.geojson",
            streets_file="streets.geojson",
            max_demand_mwh=1500,
            max_street_length_km=3.0,
            demand_share_pct=60,
        )
        builder = DijkstraPolygonBuilder(cfg)
        polygons, street_segments = builder.build()
    """

    def __init__(self, cfg: DijkstraPolygonBuilderConfig):
        super().__init__(cfg)
        self._cfg: DijkstraPolygonBuilderConfig = cfg

    def build(self) -> PolygonBuildResult:
        """
        Runs the full pipeline (street graph construction, district assignment,
        polygon synthesis) and writes all output artifacts to output_dir.
        """
        cfg = self._cfg
        dataset_name = cfg.input_dir.parent.name
        try:
            return _run_dijkstra_pipeline(
                input_dir=cfg.input_dir,
                output_dir=cfg.output_dir,
                dataset_name=dataset_name,
                request=cfg,
            )
        except Exception as exc:
            raise PolygonBuildError("Error when creating the polygons.") from exc
