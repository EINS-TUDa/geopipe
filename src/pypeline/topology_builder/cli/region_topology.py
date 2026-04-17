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

    if demand_street_indicator_column not in s.columns:
        raise ValueError(
            f"Missing demand street indicator column '{demand_street_indicator_column}' in streets file"
        )

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
    city_column: str | None,
    building_id_column: str,
    demand_building_column: str,
    demand_street_indicator_column: str,
    demand_value_column: str,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    demand_mwh = pd.to_numeric(
        streets[demand_street_indicator_column], errors="coerce"
    ).fillna(0.0) / 1_000_000.0

    pseudo_ids = pd.Series(range(len(streets)), dtype="int64")
    geometry = streets.geometry.representative_point().reset_index(drop=True)

    data: dict[str, pd.Series] = {building_id_column: pseudo_ids}
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


def run_region_topology_from_files(
    *,
    streets_file: str | Path,
    output_streets_file: str | Path,
    max_demand_mwh: float,
    max_street_length_km: float,
    demand_share_pct: float,
    output_mantra_file: str | Path | None = None,
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
    streets_path = Path(streets_file).expanduser().resolve()
    out_path = Path(output_streets_file).expanduser().resolve()

    streets = _normalize_streets_for_topology(
        gpd.read_file(streets_path),
        street_id_column=street_id_column,
        demand_street_indicator_column=str(demand_street_indicator_column),
    )

    buildings, demand_data = _buildings_and_demand_from_streets(
        streets,
        city_column=city_column,
        building_id_column=building_id_column,
        demand_building_column=demand_building_column,
        demand_street_indicator_column=str(demand_street_indicator_column),
        demand_value_column=demand_value_column,
    )

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
        description="Build region topology from streets only (buildings and demand are derived from streets).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--streets-file", required=True, help="Path to street geometries file")
    parser.add_argument("--output-streets-file", required=True, help="Output path for region-assigned streets")
    parser.add_argument("--output-mantra-file", help="Optional JSON output path for diagnostic mantra")

    parser.add_argument("--max-demand-mwh", type=float, required=True)
    parser.add_argument("--max-street-length-km", type=float, required=True)
    parser.add_argument("--demand-share-pct", type=float, required=True)

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
        streets_file=args.streets_file,
        output_streets_file=args.output_streets_file,
        output_mantra_file=args.output_mantra_file,
        max_demand_mwh=args.max_demand_mwh,
        max_street_length_km=args.max_street_length_km,
        demand_share_pct=args.demand_share_pct,
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
