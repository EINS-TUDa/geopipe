from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from pypeline.energy_system.region_topology import build_region_topology
from pypeline.plot.plotter import plot_streets_colored_by_region


def _resolve_subfolder(examples_root: Path, dataset: str) -> Path:
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


def build_for_subfolder(
    subfolder: Path,
    *,
    buildings_file: str = "debug_demand.geojson",
    streets_file: str = "linear_heat_density.geojson",
    city_column: str = "gemeindeschluessel",
    clip_buffer_m: float = 200.0,
    connect_tolerance_m: float = 10.0,
    max_demand_mwh: float = 30_000.0,
    max_street_length_km: float = 15.0,
    polynesia: bool = True,
    demand_share_pct: float = 80.0,
    small_islands: bool = True,
    small_islands_max_segments: int = 60,
    segment_streets_by_building_projections: bool = True,
    segment_projection_buffer_m: float = 12.0,
    region_id_column: str = "id",
    street_id_column: str = "street_id",
    building_id_column: str = "building_objectid",
    demand_building_column: str = "building_objectid",
    demand_source_column: str = "heating:demand[Wh]",
    demand_value_column: str = "annual_demand_mwh",
    demand_street_indicator_column: str = "total_heat_demand",
    demand_street_indicator_min: float = 0.0,
) -> tuple[Path, Path]:
    """Build district polygons for one dataset folder.

    Reads building and street GeoJSON inputs, derives building-level annual demand
    in MWh, and runs build_region_topology to partition streets into connected,
    demand-bounded regions. Writes the resulting region polygons and a topology
    preview plot into the same folder.
    """
    name = subfolder.name
    buildings_path = subfolder / buildings_file
    streets_path = subfolder / streets_file

    if not buildings_path.exists():
        raise FileNotFoundError(f"Buildings file not found: {buildings_path}")
    if not streets_path.exists():
        raise FileNotFoundError(f"Streets file not found: {streets_path}")

    buildings = gpd.read_file(buildings_path)
    streets = gpd.read_file(streets_path)

    if building_id_column not in buildings.columns:
        raise ValueError(f"Missing building id column '{building_id_column}' in {buildings_path}")
    if demand_source_column not in buildings.columns:
        raise ValueError(f"Missing demand source column '{demand_source_column}' in {buildings_path}")

    demand_by_building = pd.DataFrame(
        {
            demand_building_column: buildings[building_id_column],
            demand_value_column: pd.to_numeric(buildings[demand_source_column], errors="coerce").fillna(0.0)
            / 1_000_000.0,
        }
    )

    polygons, streets_with_region, mantra = build_region_topology(
        buildings=buildings,
        streets=streets,
        demand_data=demand_by_building,
        city_column=city_column,
        city_value=None,
        clip_buffer_m=clip_buffer_m,
        connect_tolerance_m=connect_tolerance_m,
        max_demand_mwh=max_demand_mwh,
        max_street_length_km=max_street_length_km,
        polynesia=polynesia,
        demand_share_pct=demand_share_pct,
        small_islands=small_islands,
        small_islands_max_segments=small_islands_max_segments,
        segment_streets_by_building_projections=segment_streets_by_building_projections,
        segment_projection_buffer_m=segment_projection_buffer_m,
        region_id_column=region_id_column,
        street_id_column=street_id_column,
        building_id_column=building_id_column,
        demand_building_column=demand_building_column,
        demand_value_column=demand_value_column,
        demand_street_indicator_column=demand_street_indicator_column,
        demand_street_indicator_min=demand_street_indicator_min,
    )

    polygons_out = subfolder / f"polygon_{name}.geojson"
    streets_out = subfolder / f"street_segments_{name}.geojson"
    plot_out = subfolder / f"district_topology_{name}.png"

    polygons.drop(columns=["_street_members", "_demand_street_members"], errors="ignore").to_file(polygons_out, driver="GeoJSON")
    streets_with_region.to_file(streets_out, driver="GeoJSON")
    plot_streets_colored_by_region(
        streets_with_region=streets_with_region,
        polygons=polygons,
        output_path=plot_out,
        region_id_column=region_id_column,
        title=f"District topology: {name}",
        caps_legend={
            "max_demand_mwh": f"{float(max_demand_mwh):.0f}",
            "max_street_length_km": f"{float(max_street_length_km):.1f}",
            "demand_share_pct": f"{float(demand_share_pct):.0f}%",
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
    print(f"Wrote polygons: {polygons_out} ({len(polygons)} region features)")
    print(f"Wrote district street segments: {streets_out} ({len(streets_with_region)} features)")
    print(f"Wrote topology plot: {plot_out}")

    return polygons_out, plot_out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build polygon topology for an examples subfolder")
    parser.add_argument("dataset", help="Subfolder name under examples/ (e.g., neuburg, bensheim) or full path")
    parser.add_argument("--buildings-file", default="debug_demand.geojson")
    parser.add_argument("--streets-file", default="linear_heat_density.geojson")
    parser.add_argument("--city-column", default="gemeindeschluessel")
    parser.add_argument("--max-demand-mwh", type=float, default=30_000.0)
    parser.add_argument("--max-street-length-km", type=float, default=15.0)
    parser.add_argument("--polynesia", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--demand-share-pct", type=float, default=80.0)
    parser.add_argument("--small-islands-max-segments", type=int, default=60)
    parser.add_argument("--connect-tolerance-m", type=float, default=10.0)
    parser.add_argument("--segment-projection-buffer-m", type=float, default=12.0)
    args = parser.parse_args()

    examples_root = Path(__file__).resolve().parent
    subfolder = _resolve_subfolder(examples_root, args.dataset)

    build_for_subfolder(
        subfolder,
        buildings_file=args.buildings_file,
        streets_file=args.streets_file,
        city_column=args.city_column,
        max_demand_mwh=args.max_demand_mwh,
        max_street_length_km=args.max_street_length_km,
        polynesia=bool(args.polynesia),
        demand_share_pct=args.demand_share_pct,
        small_islands_max_segments=args.small_islands_max_segments,
        connect_tolerance_m=args.connect_tolerance_m,
        segment_projection_buffer_m=args.segment_projection_buffer_m,
    )


if __name__ == "__main__":
    main()
