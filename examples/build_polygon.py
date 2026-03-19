from __future__ import annotations

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

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import geopandas as gpd
import pandas as pd

from pypeline.energy_system.region_topology import (
    RegionCaps,
    RegionTopologyConfig,
    RegionTopologyEngine,
)
from pypeline.plot.plotter import EnergySystemPlotter


@dataclass(frozen=True)
class DatasetPolygonBuildRequest:
    buildings_file: str
    streets_file: str
    max_demand_mwh: float
    max_street_length_km: float
    demand_share_pct: float
    city_column: str
    clip_buffer_m: float
    connect_tolerance_m: float
    polynesia: bool
    small_islands: bool
    small_islands_max_segments: int
    segment_streets_by_building_projections: bool
    segment_projection_buffer_m: float
    region_id_column: str
    street_id_column: str
    building_id_column: str
    demand_building_column: str
    demand_source_column: str
    demand_value_column: str
    demand_street_indicator_column: str
    demand_street_indicator_min: float


@dataclass(frozen=True)
class DatasetPolygonBuildDefaults:
    city_column: str = "gemeindeschluessel"
    clip_buffer_m: float = 200.0
    connect_tolerance_m: float = 10.0
    polynesia: bool = True
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


DATASET_POLYGON_BUILD_DEFAULTS = DatasetPolygonBuildDefaults()


@dataclass(frozen=True)
class DatasetPolygonOutputPaths:
    polygons_out: Path
    streets_out: Path
    topology_plot_out: Path
    polygon_plot_out: Path


@dataclass(frozen=True)
class DatasetTopologyBuildResult:
    polygons_path: Path
    topology_plot_path: Path
    polygon_plot_path: Path
    district_street_path: Path


class DatasetFolderResolver:

    @staticmethod
    def resolve_subfolder(examples_root: Path, dataset: str) -> Path:
        candidate = Path(dataset)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
        resolved = (examples_root / dataset).resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise FileNotFoundError(
                f"Dataset folder '{dataset}' not found. Expected a subfolder under {examples_root}."
            )
        return resolved


class DatasetPolygonBuildPipeline(DatasetFolderResolver):

    @staticmethod
    def _validate_and_normalize_request(request: DatasetPolygonBuildRequest) -> DatasetPolygonBuildRequest:
        return DatasetPolygonBuildRequest(
            buildings_file=str(request.buildings_file),
            streets_file=str(request.streets_file),
            max_demand_mwh=float(request.max_demand_mwh),
            max_street_length_km=float(request.max_street_length_km),
            demand_share_pct=float(request.demand_share_pct),
            city_column=request.city_column,
            clip_buffer_m=float(request.clip_buffer_m),
            connect_tolerance_m=float(request.connect_tolerance_m),
            polynesia=bool(request.polynesia),
            small_islands=bool(request.small_islands),
            small_islands_max_segments=int(request.small_islands_max_segments),
            segment_streets_by_building_projections=bool(request.segment_streets_by_building_projections),
            segment_projection_buffer_m=float(request.segment_projection_buffer_m),
            region_id_column=request.region_id_column,
            street_id_column=request.street_id_column,
            building_id_column=request.building_id_column,
            demand_building_column=request.demand_building_column,
            demand_source_column=request.demand_source_column,
            demand_value_column=request.demand_value_column,
            demand_street_indicator_column=request.demand_street_indicator_column,
            demand_street_indicator_min=float(request.demand_street_indicator_min),
        )

    @staticmethod
    def _resolve_input_file_path(dataset_folder: Path, file_name_or_relpath: str) -> Path:
        rel = Path(str(file_name_or_relpath))
        if rel.is_absolute():
            return rel
        candidate = dataset_folder / rel
        if candidate.exists():
            return candidate
        input_dir_candidate = dataset_folder / "input data" / rel
        if input_dir_candidate.exists():
            return input_dir_candidate
        return candidate

    @staticmethod
    def _compute_output_paths(dataset_folder: Path, *, polynesia: bool) -> DatasetPolygonOutputPaths:
        name = dataset_folder.name
        has_input_dir = (dataset_folder / "input data").exists()
        has_output_dir = (dataset_folder / "output data").exists()
        outputs_dir = (dataset_folder / "output data") if (has_input_dir or has_output_dir) else dataset_folder
        outputs_dir.mkdir(parents=True, exist_ok=True)
        polygons_out = outputs_dir / f"polygon_{name}.geojson"
        streets_out = outputs_dir / f"street_segments_{name}.geojson"
        plots_dir = outputs_dir / "plots"
        plots_dir.mkdir(parents=True, exist_ok=True)
        plot_suffix = "_polynesia" if polynesia else ""
        topology_plot_out = plots_dir / f"district_topology_{name}{plot_suffix}.png"
        polygon_plot_out = plots_dir / f"district_polygons_{name}{plot_suffix}.png"
        return DatasetPolygonOutputPaths(
            polygons_out=polygons_out,
            streets_out=streets_out,
            topology_plot_out=topology_plot_out,
            polygon_plot_out=polygon_plot_out,
        )

    @staticmethod
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

    @staticmethod
    def build_polygons_for_dataset_folder(dataset_folder: Path, *, request: DatasetPolygonBuildRequest) -> tuple[Path, Path, Path]:
        cfg = DatasetPolygonBuildPipeline._validate_and_normalize_request(request)

        buildings_path = DatasetPolygonBuildPipeline._resolve_input_file_path(dataset_folder, str(cfg.buildings_file))
        streets_path = DatasetPolygonBuildPipeline._resolve_input_file_path(dataset_folder, str(cfg.streets_file))

        if not buildings_path.exists():
            raise FileNotFoundError(f"Buildings file not found: {buildings_path}")
        if not streets_path.exists():
            raise FileNotFoundError(f"Streets file not found: {streets_path}")

        buildings = gpd.read_file(buildings_path)
        streets = gpd.read_file(streets_path)

        if cfg.building_id_column not in buildings.columns:
            raise ValueError(f"Missing building id column '{cfg.building_id_column}' in {buildings_path}")
        if cfg.demand_source_column not in buildings.columns:
            raise ValueError(f"Missing demand source column '{cfg.demand_source_column}' in {buildings_path}")

        demand_by_building = pd.DataFrame(
            {
                cfg.demand_building_column: buildings[cfg.building_id_column],
                cfg.demand_value_column: pd.to_numeric(buildings[cfg.demand_source_column], errors="coerce").fillna(0.0) / 1_000_000.0,
            }
        )

        b_for_algo = buildings.copy()
        city_value = None
        if cfg.city_column and cfg.city_column in buildings.columns and (cfg.city_column in streets.columns):
            vals = buildings[cfg.city_column].dropna().astype(str)
            if vals.empty:
                raise ValueError(f"No values in city column '{cfg.city_column}'")
            city_value = str(vals.mode().iloc[0])
            b_for_algo = buildings[buildings[cfg.city_column].astype(str) == str(city_value)].copy()

        topology_config = RegionTopologyConfig.from_required_caps(
            max_demand_mwh=float(cfg.max_demand_mwh),
            max_street_length_km=float(cfg.max_street_length_km),
            demand_share_pct=float(cfg.demand_share_pct),
            city_column=cfg.city_column,
            city_value=city_value,
            clip_buffer_m=float(cfg.clip_buffer_m),
            connect_tolerance_m=float(cfg.connect_tolerance_m),
            polynesia=bool(cfg.polynesia),
            small_islands=bool(cfg.small_islands),
            small_islands_max_segments=int(cfg.small_islands_max_segments),
            segment_streets_by_building_projections=bool(cfg.segment_streets_by_building_projections),
            segment_projection_buffer_m=float(cfg.segment_projection_buffer_m),
            region_id_column=cfg.region_id_column,
            street_id_column=cfg.street_id_column,
            building_id_column=cfg.building_id_column,
            demand_building_column=cfg.demand_building_column,
            demand_value_column=cfg.demand_value_column,
            demand_street_indicator_column=cfg.demand_street_indicator_column,
            demand_street_indicator_min=float(cfg.demand_street_indicator_min),
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
            caps=RegionCaps(max_demand_mwh=float(cfg.max_demand_mwh), max_street_length_km=float(cfg.max_street_length_km), max_nondemand_street_km=None),
            config=topology_config,
        )

        artifacts = DatasetPolygonBuildPipeline._compute_output_paths(dataset_folder, polynesia=cfg.polynesia)

        polygons.drop(columns=["_street_members", "_demand_street_members"], errors="ignore").to_file(artifacts.polygons_out, driver="GeoJSON")
        streets_with_region.to_file(artifacts.streets_out, driver="GeoJSON")

        EnergySystemPlotter.plot_streets_colored_by_region(
            streets_with_region=streets_with_region,
            polygons=polygons,
            output_path=artifacts.topology_plot_out,
            region_id_column=cfg.region_id_column,
            title=f"District topology: {dataset_folder.name}",
            caps_legend={
                "max_demand_mwh": f"{float(cfg.max_demand_mwh):.0f}",
                "max_street_length_km": f"{float(cfg.max_street_length_km):.1f}",
                "demand_share_pct": f"{float(cfg.demand_share_pct):.0f}%",
            },
        )

        DatasetPolygonBuildPipeline._plot_polygons(
            polygons,
            region_id_column=cfg.region_id_column,
            title=f"District polygons: {dataset_folder.name}",
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
        print(f"Wrote polygons: {artifacts.polygons_out} ({len(polygons)} region features)")
        print(f"Wrote district street segments: {artifacts.streets_out} ({len(streets_with_region)} features)")
        print(f"Wrote topology plot: {artifacts.topology_plot_out}")
        print(f"Wrote polygon plot: {artifacts.polygon_plot_out}")

        return artifacts.polygons_out, artifacts.topology_plot_out, artifacts.streets_out


def create_dataset_polygon_build_request(
    *,
    buildings_file: str,
    streets_file: str,
    max_demand_mwh: float,
    max_street_length_km: float,
    demand_share_pct: float,
    polynesia: bool | None = None,
    city_column: str | None = None,
    clip_buffer_m: float | None = None,
    connect_tolerance_m: float | None = None,
    small_islands: bool | None = None,
    small_islands_max_segments: int | None = None,
    segment_streets_by_building_projections: bool | None = None,
    segment_projection_buffer_m: float | None = None,
    region_id_column: str | None = None,
    street_id_column: str | None = None,
    building_id_column: str | None = None,
    demand_building_column: str | None = None,
    demand_source_column: str | None = None,
    demand_value_column: str | None = None,
    demand_street_indicator_column: str | None = None,
    demand_street_indicator_min: float | None = None,
) -> DatasetPolygonBuildRequest:
    return DatasetPolygonBuildRequest(
        buildings_file=buildings_file,
        streets_file=streets_file,
        max_demand_mwh=float(max_demand_mwh),
        max_street_length_km=float(max_street_length_km),
        demand_share_pct=float(demand_share_pct),
        city_column=DATASET_POLYGON_BUILD_DEFAULTS.city_column if city_column is None else city_column,
        clip_buffer_m=DATASET_POLYGON_BUILD_DEFAULTS.clip_buffer_m if clip_buffer_m is None else float(clip_buffer_m),
        connect_tolerance_m=DATASET_POLYGON_BUILD_DEFAULTS.connect_tolerance_m if connect_tolerance_m is None else float(connect_tolerance_m),
        polynesia=DATASET_POLYGON_BUILD_DEFAULTS.polynesia if polynesia is None else bool(polynesia),
        small_islands=DATASET_POLYGON_BUILD_DEFAULTS.small_islands if small_islands is None else bool(small_islands),
        small_islands_max_segments=DATASET_POLYGON_BUILD_DEFAULTS.small_islands_max_segments if small_islands_max_segments is None else int(small_islands_max_segments),
        segment_streets_by_building_projections=DATASET_POLYGON_BUILD_DEFAULTS.segment_streets_by_building_projections if segment_streets_by_building_projections is None else bool(segment_streets_by_building_projections),
        segment_projection_buffer_m=DATASET_POLYGON_BUILD_DEFAULTS.segment_projection_buffer_m if segment_projection_buffer_m is None else float(segment_projection_buffer_m),
        region_id_column=DATASET_POLYGON_BUILD_DEFAULTS.region_id_column if region_id_column is None else region_id_column,
        street_id_column=DATASET_POLYGON_BUILD_DEFAULTS.street_id_column if street_id_column is None else street_id_column,
        building_id_column=DATASET_POLYGON_BUILD_DEFAULTS.building_id_column if building_id_column is None else building_id_column,
        demand_building_column=DATASET_POLYGON_BUILD_DEFAULTS.demand_building_column if demand_building_column is None else demand_building_column,
        demand_source_column=DATASET_POLYGON_BUILD_DEFAULTS.demand_source_column if demand_source_column is None else demand_source_column,
        demand_value_column=DATASET_POLYGON_BUILD_DEFAULTS.demand_value_column if demand_value_column is None else demand_value_column,
        demand_street_indicator_column=DATASET_POLYGON_BUILD_DEFAULTS.demand_street_indicator_column if demand_street_indicator_column is None else demand_street_indicator_column,
        demand_street_indicator_min=DATASET_POLYGON_BUILD_DEFAULTS.demand_street_indicator_min if demand_street_indicator_min is None else float(demand_street_indicator_min),
    )


def build_dataset_topology_outputs(dataset_folder: Path, *, request: DatasetPolygonBuildRequest) -> DatasetTopologyBuildResult:
    polygons_out, _ ,streets_out = DatasetPolygonBuildPipeline.build_polygons_for_dataset_folder(dataset_folder, request=request)
    artifacts = DatasetPolygonBuildPipeline._compute_output_paths(dataset_folder, polynesia=bool(request.polynesia))

    return DatasetTopologyBuildResult(
        polygons_path=polygons_out,
        topology_plot_path=artifacts.topology_plot_out,
        polygon_plot_path=artifacts.polygon_plot_out,
        district_street_path=streets_out,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build polygon topology for an examples subfolder")
    parser.add_argument("dataset", help="Subfolder name under examples/ (e.g., neuburg, bensheim) or full path")
    parser.add_argument("--buildings-file", required=True)
    parser.add_argument("--streets-file", required=True)
    parser.add_argument("--city-column", default=DATASET_POLYGON_BUILD_DEFAULTS.city_column)
    parser.add_argument("--max-demand-mwh", type=float, required=True)
    parser.add_argument("--max-street-length-km", type=float, required=True)
    parser.add_argument("--polynesia", action=argparse.BooleanOptionalAction, default=DATASET_POLYGON_BUILD_DEFAULTS.polynesia)
    parser.add_argument("--demand-share-pct", type=float, required=True)
    parser.add_argument("--small-islands-max-segments", type=int, default=DATASET_POLYGON_BUILD_DEFAULTS.small_islands_max_segments)
    parser.add_argument("--connect-tolerance-m", type=float, default=DATASET_POLYGON_BUILD_DEFAULTS.connect_tolerance_m)
    parser.add_argument("--segment-projection-buffer-m", type=float, default=DATASET_POLYGON_BUILD_DEFAULTS.segment_projection_buffer_m)
    args = parser.parse_args()

    examples_root = Path(__file__).resolve().parent
    dataset_folder = DatasetFolderResolver.resolve_subfolder(examples_root, args.dataset)

    request = create_dataset_polygon_build_request(
        buildings_file=args.buildings_file,
        streets_file=args.streets_file,
        max_demand_mwh=float(args.max_demand_mwh),
        max_street_length_km=float(args.max_street_length_km),
        demand_share_pct=float(args.demand_share_pct),
        city_column=args.city_column,
        connect_tolerance_m=float(args.connect_tolerance_m),
        polynesia=bool(args.polynesia),
        small_islands_max_segments=int(args.small_islands_max_segments),
        segment_projection_buffer_m=float(args.segment_projection_buffer_m),
    )

    DatasetPolygonBuildPipeline.build_polygons_for_dataset_folder(dataset_folder, request=request)


if __name__ == "__main__":
    main()
