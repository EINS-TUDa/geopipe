from __future__ import annotations

from pathlib import Path

from examples.run_models import DijkstraScenarioConfig, ScenarioRunOutput
from examples.example_api import run_dijkstra_scenario


project_root = Path(__file__).resolve().parents[2]
BENSHEIM_DIR = project_root / "examples" / "bensheim"

SCENARIO_CONFIG = DijkstraScenarioConfig(
    project_root=project_root,
    input_dir=BENSHEIM_DIR,
    output_dir=BENSHEIM_DIR / "output_data",
    buildings_file="wah_bensheim_4_districts.geojson",
    streets_file="baublock_bensheim_epsg25832.geojson",
    heat_demand_file=project_root / "data" / "WaermeatlasHessen.gpkg",
    heating_shares_file=project_root / "data" / "Census2022HeatingType100mGrid" / "Census2022HeatingType100mGrid_Polygons_southhessen.geojson",
    model_name="Bensheim",
    scenario_name="Base4twk",
    tss_name="4ThinWeeks",
    demand_name="residential_heat",
    start_year=2020,
    end_year=2030,
    year_gap=5,
    retain_existing_output_drop_per_year=0.05,
    lockout_years=2,
    max_demand_mwh=15_000.0,
    max_street_length_km=15.0,
    demand_share_pct=75.0,
    polynesia=False,
    city_column="gemeindeschluessel",
    output_plots_dir=BENSHEIM_DIR / "plots",
    region_builder_config_overrides={
        "min_heat_grid_share": 0.20,
        "heat_grid_names": ("heat_exchanger",),
    },
)


def main() -> ScenarioRunOutput:
    run_output = run_dijkstra_scenario(SCENARIO_CONFIG)
    report_path = run_output.results_report_html
    if report_path is not None:
        print(f"CESM report: {report_path}")
    return run_output


if __name__ == "__main__":
    main()
