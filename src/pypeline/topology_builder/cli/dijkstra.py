"""CLI entrypoint for dataset-based Dijkstra topology building."""

from __future__ import annotations

import argparse
from pathlib import Path

from pypeline.topology_builder.core import TopologyBuildResult
from pypeline.topology_builder.dijkstra_builder import (
    DijkstraTopologyBuilder,
    DijkstraTopologyBuilderConfig,
)


_DEFAULTS = DijkstraTopologyBuilderConfig(
    input_dir=Path(),
    output_dir=Path(),
    streets_file="streets.geojson",
    max_demand_mwh=15_000.0,
    max_street_length_km=15.0,
    demand_share_pct=75.0,
)


def _resolve_dataset_folder(dataset: str) -> Path:
    candidate = Path(dataset)
    if candidate.is_absolute():
        resolved = candidate
    else:
        resolved = Path.cwd() / candidate
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(
            f"Dataset folder '{dataset}' not found (resolved to: {resolved})."
        )
    return resolved.resolve()


def build_topology_from_dataset(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    streets_file: str,
    max_demand_mwh: float,
    max_street_length_km: float,
    demand_share_pct: float,
    city_column: str = _DEFAULTS.city_column,
    polynesia: bool = _DEFAULTS.polynesia,
    small_islands_max_segments: int = _DEFAULTS.small_islands_max_segments,
    connect_tolerance_m: float = _DEFAULTS.connect_tolerance_m,
    segment_projection_buffer_m: float = _DEFAULTS.segment_projection_buffer_m,
) -> TopologyBuildResult:
    cfg = DijkstraTopologyBuilderConfig(
        input_dir=Path(input_dir).expanduser().resolve(),
        output_dir=Path(output_dir).expanduser().resolve(),
        streets_file=streets_file,
        max_demand_mwh=max_demand_mwh,
        max_street_length_km=max_street_length_km,
        demand_share_pct=demand_share_pct,
        city_column=city_column,
        polynesia=polynesia,
        small_islands_max_segments=small_islands_max_segments,
        connect_tolerance_m=connect_tolerance_m,
        segment_projection_buffer_m=segment_projection_buffer_m,
    )
    return DijkstraTopologyBuilder.from_config(cfg).build()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build district topology graphs for a dataset folder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("dataset", help="Path to the dataset folder (must contain input_data/).")
    parser.add_argument("--streets-file", required=True, help="Streets GeoJSON filename inside input_data/.")
    parser.add_argument("--max-demand-mwh", type=float, required=True, help="Maximum heat demand per district in MWh.")
    parser.add_argument("--max-street-length-km", type=float, required=True, help="Maximum street length per district in km.")
    parser.add_argument("--demand-share-pct", type=float, required=True, help="Minimum demand share percentage per district.")
    parser.add_argument("--city-column", default=_DEFAULTS.city_column)
    parser.add_argument("--polynesia", action=argparse.BooleanOptionalAction, default=_DEFAULTS.polynesia)
    parser.add_argument("--small-islands-max-segments", type=int, default=_DEFAULTS.small_islands_max_segments)
    parser.add_argument("--connect-tolerance-m", type=float, default=_DEFAULTS.connect_tolerance_m)
    parser.add_argument("--segment-projection-buffer-m", type=float, default=_DEFAULTS.segment_projection_buffer_m)
    args = parser.parse_args()

    dataset_folder = _resolve_dataset_folder(args.dataset)
    input_dir = dataset_folder / "input_data"
    output_dir = dataset_folder / "output_data"

    if not input_dir.exists():
        raise FileNotFoundError(f"input_data/ directory not found in: {dataset_folder}")

    build_topology_from_dataset(
        input_dir=input_dir,
        output_dir=output_dir,
        streets_file=args.streets_file,
        max_demand_mwh=args.max_demand_mwh,
        max_street_length_km=args.max_street_length_km,
        demand_share_pct=args.demand_share_pct,
        city_column=args.city_column,
        polynesia=args.polynesia,
        small_islands_max_segments=args.small_islands_max_segments,
        connect_tolerance_m=args.connect_tolerance_m,
        segment_projection_buffer_m=args.segment_projection_buffer_m,
    )


if __name__ == "__main__":
    main()
