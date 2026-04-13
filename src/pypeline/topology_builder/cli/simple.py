"""CLI entrypoint for scenario-based simple topology building."""

from __future__ import annotations

import argparse
from pathlib import Path

from pypeline.topology_builder.simple_builder import (
    SimpleTopologyBuilder,
    SimpleTopologyBuilderConfig,
)
from pypeline.topology_builder.core import TopologyBuildResult


def _resolve_case_folder(case: str) -> Path:
    candidate = Path(case)
    if candidate.is_absolute():
        resolved = candidate
    else:
        resolved = Path.cwd() / candidate
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(
            f"Case folder '{case}' not found (resolved to: {resolved})."
        )
    return resolved.resolve()


def _resolve_case_file(case_folder: Path, file_name_or_relpath: str) -> Path:
    rel = Path(file_name_or_relpath)
    if rel.is_absolute():
        return rel
    return case_folder / rel


def build_topology_from_case(
    *,
    case: str | Path,
    streets_file: str,
    scenario_file: str,
    region_id_column: str = "id",
    street_id_column: str = "street_id",
    demand_column: str = "total_heat_demand",
    street_length_column: str = "street_length",
    apply_injections: bool = True,
) -> TopologyBuildResult:
    case_folder = _resolve_case_folder(str(case))
    resolved_streets = _resolve_case_file(case_folder, streets_file)
    resolved_scenario = _resolve_case_file(case_folder, scenario_file)

    if not resolved_streets.exists():
        raise FileNotFoundError(f"Streets file not found: {resolved_streets}")
    if not resolved_scenario.exists():
        raise FileNotFoundError(f"Scenario file not found: {resolved_scenario}")

    cfg = SimpleTopologyBuilderConfig(
        streets_file=resolved_streets,
        scenario_file=resolved_scenario,
        region_id_column=region_id_column,
        street_id_column=street_id_column,
        demand_column=demand_column,
        street_length_column=street_length_column,
        apply_injections=apply_injections,
    )
    return SimpleTopologyBuilder.from_config(cfg).build()


def main() -> None:
    _defaults = SimpleTopologyBuilderConfig(
        streets_file=Path(),
        scenario_file=Path(),
    )

    parser = argparse.ArgumentParser(
        description="Build topology from a scenario YAML and street segments.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("case", help="Path to the case folder.")
    parser.add_argument("--streets-file", required=True, help="Street segments file path relative to case folder (or absolute).")
    parser.add_argument("--scenario-file", required=True, help="Scenario YAML path relative to case folder (or absolute).")
    parser.add_argument("--region-id-column", default=_defaults.region_id_column)
    parser.add_argument("--street-id-column", default=_defaults.street_id_column)
    parser.add_argument("--demand-column", default=_defaults.demand_column)
    parser.add_argument("--street-length-column", default=_defaults.street_length_column)
    parser.add_argument("--apply-injections", action=argparse.BooleanOptionalAction, default=_defaults.apply_injections)
    args = parser.parse_args()

    build_topology_from_case(
        case=args.case,
        streets_file=args.streets_file,
        scenario_file=args.scenario_file,
        region_id_column=args.region_id_column,
        street_id_column=args.street_id_column,
        demand_column=args.demand_column,
        street_length_column=args.street_length_column,
        apply_injections=args.apply_injections,
    )


if __name__ == "__main__":
    main()