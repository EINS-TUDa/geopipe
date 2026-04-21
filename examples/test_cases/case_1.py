from __future__ import annotations
from pathlib import Path

from examples.example_runner import (
    ScenarioCaseConfig,
    ScenarioExecutionResult,
    run_scenario_case,
)

project_root = Path(__file__).resolve().parents[2]
CASE_DIR = Path(__file__).resolve().parent

CASE_CONFIG = ScenarioCaseConfig(
    project_root=project_root,
    scenario_file=CASE_DIR / "case_1.yaml",
    streets_file=CASE_DIR / "input_data" / "linear_heat_density.geojson",
    heating_shares_file=CASE_DIR / "input_data" / "heating_shares_neuburg.geojson",
    model_name="Case1",
    scenario_name="BaseCase1",
    tss_name="4ThinWeeks",
    dt_hours=4,
    demand_name="residential_heat",
    start_year=2020,
    end_year=2040,
    year_gap=5,
    retain_existing_output_drop_per_year=0.05,
    lockout_years=2,
    commodity_activation_year_by_name={"hydrogen": 2035},
    technology_activation_year_by_name={"ind_heat_pump": 2035},
    apply_injections=True,
    default_central_tech="CHP",
    min_distance = 5,
    region_builder_config_overrides={
        "min_heat_grid_share": 0.1,
        "heat_grid_names": ("heat_exchanger",),
        "interdistrict_free_pipe_max_length_m": 10.0,
    },
    expected_region_ids=(0, 1),
)


def main(*, apply_injections: bool | None = None) -> ScenarioExecutionResult:
    summary = run_scenario_case(CASE_CONFIG, apply_injections=apply_injections)
    print("Case 1 completed.")
    print(f"regions={summary.regions}, network_edges={summary.network_edges}")
    print(f"injected_demand={summary.injected_demand:.2f}, injected_tech_count={summary.injected_tech_count}")
    return summary


if __name__ == "__main__":
    main()
