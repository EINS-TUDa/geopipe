"""CLI entrypoint for region topology builder."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import geopandas as gpd
import pandas as pd

from pypeline.topology_builder.region_topology_builder import (
    REGION_TOPOLOGY_DEFAULTS,
    RegionTopologyConfig,
    build_region_topology,
)


def _read_demand_data(path: Path) -> pd.DataFrame | gpd.GeoDataFrame:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return gpd.read_file(path)


def _derive_demand_data_from_buildings(
    buildings: gpd.GeoDataFrame,
    *,
    building_id_column: str,
    demand_building_column: str,
    demand_value_column: str,
    demand_source_column: str,
) -> pd.DataFrame:
    if building_id_column not in buildings.columns:
        raise ValueError(f"Missing building id column '{building_id_column}' in buildings data")
    if demand_source_column not in buildings.columns:
        raise ValueError(
            f"Missing demand source column '{demand_source_column}' in buildings data. "
            "Provide --demand-file or adjust --demand-source-column."
        )

    demand_wh = pd.to_numeric(buildings[demand_source_column], errors="coerce").fillna(0.0)
    return pd.DataFrame(
        {
            demand_building_column: buildings[building_id_column],
            demand_value_column: demand_wh / 1_000_000.0,
        }
    )


def run_region_topology_from_files(
    *,
    buildings_file: str | Path,
    streets_file: str | Path,
    output_streets_file: str | Path,
    max_demand_mwh: float,
    max_street_length_km: float,
    demand_share_pct: float,
    demand_file: str | Path | None = None,
    output_mantra_file: str | Path | None = None,
    demand_source_column: str = "heating:demand[Wh]",
    city_column: str | None = REGION_TOPOLOGY_DEFAULTS.city_column,
    city_value: str | None = REGION_TOPOLOGY_DEFAULTS.city_value,
    clip_buffer_m: float = REGION_TOPOLOGY_DEFAULTS.clip_buffer_m,
    connect_tolerance_m: float = REGION_TOPOLOGY_DEFAULTS.connect_tolerance_m,
    polynesia: bool = REGION_TOPOLOGY_DEFAULTS.polynesia,
    small_islands: bool = REGION_TOPOLOGY_DEFAULTS.small_islands,
    small_islands_max_segments: int = REGION_TOPOLOGY_DEFAULTS.small_islands_max_segments,
    segment_streets_by_building_projections: bool = REGION_TOPOLOGY_DEFAULTS.segment_streets_by_building_projections,
    segment_projection_buffer_m: float = REGION_TOPOLOGY_DEFAULTS.segment_projection_buffer_m,
    region_id_column: str = REGION_TOPOLOGY_DEFAULTS.region_id_column,
    street_id_column: str = REGION_TOPOLOGY_DEFAULTS.street_id_column,
    building_id_column: str = REGION_TOPOLOGY_DEFAULTS.building_id_column,
    demand_building_column: str = REGION_TOPOLOGY_DEFAULTS.demand_building_column,
    demand_value_column: str = REGION_TOPOLOGY_DEFAULTS.demand_value_column,
    demand_street_indicator_column: str | None = REGION_TOPOLOGY_DEFAULTS.demand_street_indicator_column,
    demand_street_indicator_min: float = REGION_TOPOLOGY_DEFAULTS.demand_street_indicator_min,
) -> tuple[Path, dict[str, int]]:
    buildings_path = Path(buildings_file).expanduser().resolve()
    streets_path = Path(streets_file).expanduser().resolve()
    out_path = Path(output_streets_file).expanduser().resolve()

    buildings = gpd.read_file(buildings_path)
    streets = gpd.read_file(streets_path)

    if demand_file is None:
        demand_data = _derive_demand_data_from_buildings(
            buildings,
            building_id_column=building_id_column,
            demand_building_column=demand_building_column,
            demand_value_column=demand_value_column,
            demand_source_column=demand_source_column,
        )
    else:
        demand_data = _read_demand_data(Path(demand_file).expanduser().resolve())

    config = RegionTopologyConfig.from_required_caps(
        max_demand_mwh=max_demand_mwh,
        max_street_length_km=max_street_length_km,
        demand_share_pct=demand_share_pct,
        city_column=city_column,
        city_value=city_value,
        clip_buffer_m=clip_buffer_m,
        connect_tolerance_m=connect_tolerance_m,
        polynesia=polynesia,
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

    streets_with_region, mantra = build_region_topology(
        buildings=buildings,
        streets=streets,
        demand_data=demand_data,
        config=config,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".gpkg":
        streets_with_region.to_file(out_path, driver="GPKG")
    else:
        streets_with_region.to_file(out_path)

    if output_mantra_file is not None:
        mantra_path = Path(output_mantra_file).expanduser().resolve()
        mantra_path.parent.mkdir(parents=True, exist_ok=True)
        with mantra_path.open("w", encoding="utf-8") as handle:
            json.dump(mantra, handle, indent=2, sort_keys=True)

    return out_path, mantra


def main() -> None:
    defaults = REGION_TOPOLOGY_DEFAULTS

    parser = argparse.ArgumentParser(
        description="Build region topology from buildings, streets, and demand data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--buildings-file", required=True, help="Path to building geometries file")
    parser.add_argument("--streets-file", required=True, help="Path to street geometries file")
    parser.add_argument("--demand-file", help="Optional demand file (CSV/Parquet/Geo file)")
    parser.add_argument("--output-streets-file", required=True, help="Output path for region-assigned streets")
    parser.add_argument("--output-mantra-file", help="Optional JSON output path for diagnostic mantra")

    parser.add_argument("--max-demand-mwh", type=float, required=True)
    parser.add_argument("--max-street-length-km", type=float, required=True)
    parser.add_argument("--demand-share-pct", type=float, required=True)

    parser.add_argument("--demand-source-column", default="heating:demand[Wh]")

    parser.add_argument("--city-column", default=defaults.city_column)
    parser.add_argument("--city-value", default=defaults.city_value)
    parser.add_argument("--clip-buffer-m", type=float, default=defaults.clip_buffer_m)
    parser.add_argument("--connect-tolerance-m", type=float, default=defaults.connect_tolerance_m)
    parser.add_argument("--polynesia", action=argparse.BooleanOptionalAction, default=defaults.polynesia)
    parser.add_argument("--small-islands", action=argparse.BooleanOptionalAction, default=defaults.small_islands)
    parser.add_argument("--small-islands-max-segments", type=int, default=defaults.small_islands_max_segments)
    parser.add_argument(
        "--segment-streets-by-building-projections",
        action=argparse.BooleanOptionalAction,
        default=defaults.segment_streets_by_building_projections,
    )
    parser.add_argument("--segment-projection-buffer-m", type=float, default=defaults.segment_projection_buffer_m)

    parser.add_argument("--region-id-column", default=defaults.region_id_column)
    parser.add_argument("--street-id-column", default=defaults.street_id_column)
    parser.add_argument("--building-id-column", default=defaults.building_id_column)
    parser.add_argument("--demand-building-column", default=defaults.demand_building_column)
    parser.add_argument("--demand-value-column", default=defaults.demand_value_column)
    parser.add_argument("--demand-street-indicator-column", default=defaults.demand_street_indicator_column)
    parser.add_argument("--demand-street-indicator-min", type=float, default=defaults.demand_street_indicator_min)

    args = parser.parse_args()

    out_path, mantra = run_region_topology_from_files(
        buildings_file=args.buildings_file,
        streets_file=args.streets_file,
        demand_file=args.demand_file,
        output_streets_file=args.output_streets_file,
        output_mantra_file=args.output_mantra_file,
        max_demand_mwh=args.max_demand_mwh,
        max_street_length_km=args.max_street_length_km,
        demand_share_pct=args.demand_share_pct,
        demand_source_column=args.demand_source_column,
        city_column=args.city_column,
        city_value=args.city_value,
        clip_buffer_m=args.clip_buffer_m,
        connect_tolerance_m=args.connect_tolerance_m,
        polynesia=args.polynesia,
        small_islands=args.small_islands,
        small_islands_max_segments=args.small_islands_max_segments,
        segment_streets_by_building_projections=args.segment_streets_by_building_projections,
        segment_projection_buffer_m=args.segment_projection_buffer_m,
        region_id_column=args.region_id_column,
        street_id_column=args.street_id_column,
        building_id_column=args.building_id_column,
        demand_building_column=args.demand_building_column,
        demand_value_column=args.demand_value_column,
        demand_street_indicator_column=args.demand_street_indicator_column,
        demand_street_indicator_min=args.demand_street_indicator_min,
    )

    print(f"Wrote region-assigned streets: {out_path}")


if __name__ == "__main__":
    main()
