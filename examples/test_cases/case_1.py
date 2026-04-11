from __future__ import annotations
from pathlib import Path

from examples.run_models import ScenarioCaseConfig, ScenarioRunOutput
from examples.example_api import render_topology_plot, run_scenario_case

project_root = Path(__file__).resolve().parents[2]
CASE_DIR = Path(__file__).resolve().parent

CASE_CONFIG = ScenarioCaseConfig(
    project_root=project_root,
    scenario_file=CASE_DIR / "case_1.yaml",
    streets_file=CASE_DIR / "input_data" / "linear_heat_density.geojson",
    heat_demand_file=CASE_DIR / "input_data" / "buildings_heat_demand.geojson",
    heating_shares_file=CASE_DIR / "input_data" / "heating_shares_neuburg.geojson",
    model_name="Case1",
    scenario_name="BaseCase1",
    tss_name="4ThinWeeks",
    demand_name="residential_heat",
    start_year=2020,
    end_year=2030,
    year_gap=5,
    retain_existing_output_drop_per_year=0.05,
    lockout_years=2,
    apply_injections=True,
    region_builder_config_overrides={
        "min_heat_grid_share": 0.0,
        "heat_grid_names": ("heat_exchanger",),
    },
    output_plots_dir=CASE_DIR / "output_data" / "plots",
    expected_region_ids=(0, 1),
)


def main() -> ScenarioRunOutput:
    summary = run_scenario_case(CASE_CONFIG)
    print("Case 1 completed.")
    print(f"regions={summary.regions}, network_edges={summary.network_edges}")
    print(f"injected_demand={summary.injected_demand:.2f}, injected_tech_count={summary.injected_tech_count}")
    return summary


if __name__ == "__main__":
    run_output = main()
    render_topology_plot(run_output)
