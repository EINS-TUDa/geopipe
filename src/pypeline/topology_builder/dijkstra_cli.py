from __future__ import annotations

"""
CLI entry point for building district topology graphs.

All pipeline logic lives in pypeline.topology_builder.dijkstra_builder.

Usage:
    python -m pypeline.topology_builder.dijkstra_cli <dataset_folder> \
        --buildings-file buildings_heat_demand.geojson \
        --streets-file linear_heat_density.geojson \
        --max-demand-mwh 15000 \
        --max-street-length-km 15 \
        --demand-share-pct 75

<dataset_folder> must contain an input_data/ subdirectory.
Output is written to <dataset_folder>/output_data/.
"""

import argparse
from pathlib import Path

from pypeline.topology_builder.dijkstra_builder import (
    DijkstraTopologyBuilder,
    DijkstraTopologyBuilderConfig,
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


def main() -> None:
    # Use a temporary default instance to read the field defaults — avoids
    # duplicating the default values here in the CLI.
    _defaults = DijkstraTopologyBuilderConfig(
        input_dir=Path(),
        output_dir=Path(),
        buildings_file="buildings.geojson",
        streets_file="streets.geojson",
        max_demand_mwh=15_000.0,
        max_street_length_km=15.0,
        demand_share_pct=75.0,
    )

    parser = argparse.ArgumentParser(
        description="Build district topology graphs for a dataset folder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("dataset", help="Path to the dataset folder (must contain input_data/).")
    parser.add_argument("--buildings-file", required=True, help="Buildings GeoJSON filename inside input_data/.")
    parser.add_argument("--streets-file", required=True, help="Streets GeoJSON filename inside input_data/.")
    parser.add_argument("--max-demand-mwh", type=float, required=True, help="Maximum heat demand per district in MWh.")
    parser.add_argument("--max-street-length-km", type=float, required=True, help="Maximum street length per district in km.")
    parser.add_argument("--demand-share-pct", type=float, required=True, help="Minimum demand share percentage per district.")
    parser.add_argument("--city-column", default=_defaults.city_column)
    parser.add_argument("--polynesia", action=argparse.BooleanOptionalAction, default=_defaults.polynesia)
    parser.add_argument("--small-islands-max-segments", type=int, default=_defaults.small_islands_max_segments)
    parser.add_argument("--connect-tolerance-m", type=float, default=_defaults.connect_tolerance_m)
    parser.add_argument("--segment-projection-buffer-m", type=float, default=_defaults.segment_projection_buffer_m)
    args = parser.parse_args()

    dataset_folder = _resolve_dataset_folder(args.dataset)
    input_dir = dataset_folder / "input_data"
    output_dir = dataset_folder / "output_data"

    if not input_dir.exists():
        raise FileNotFoundError(f"input_data/ directory not found in: {dataset_folder}")

    cfg = DijkstraTopologyBuilderConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        buildings_file=args.buildings_file,
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

    DijkstraTopologyBuilder(cfg).build()


if __name__ == "__main__":
    main()
