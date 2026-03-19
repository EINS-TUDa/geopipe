from pathlib import Path
project_root = Path(__file__).resolve().parents[2]
from pypeline.plot.plotter import EnergySystemPlotter
from examples.example_builders import (
    prepare_topology_outputs,
    confirm_continue,
    load_spatial_inputs,
    create_cesm_backend,
    years_for_scenario,
    plot_mix_results,
    show_cesm_sankey,
    build_energy_system,
    build_scenario
)

project_root = Path(__file__).resolve().parents[2]
BENSHEIM_DIR = project_root / "examples" / "bensheim"
BENSHEIM_PLOTS_DIR = BENSHEIM_DIR / "plots"
BENSHEIM_PLOTS_DIR.mkdir(parents=True, exist_ok=True)

BUILDINGS_FILE = "wah_bensheim_4_districts.geojson"
STREETS_FILE = "baublock_bensheim_epsg25832.geojson"
HEAT_DEMAND_FILE = project_root / "data" / "WaermeatlasHessen.gpkg"
HEATING_SHARES_FILE = project_root / "data" / "Census2022HeatingType100mGrid" / "Census2022HeatingType100mGrid_Polygons_southhessen.geojson"

MODEL_NAME = "Bensheim"
SCENARIO_NAME = "Base4twk"
TSS_NAME = "4ThinWeeks"
DEMAND_NAME = "residential_heat"

def main():
    ##################
    # Topology block
    ##################
    polygons_path, polygon_plot_path = prepare_topology_outputs(
        BENSHEIM_DIR,
        buildings_file=BUILDINGS_FILE,
        streets_file=STREETS_FILE,
        max_demand_mwh=15_000.0,
        max_street_length_km=15.0,
        demand_share_pct=75.0,
        polynesia=False,
    )
    if not confirm_continue(polygon_plot_path):
        return
    polygons, district_street_segments = load_spatial_inputs(BENSHEIM_DIR, polygons_path, STREETS_FILE)

    #############
    # ESM block
    #############
    es = build_energy_system(
        model_name=MODEL_NAME,
        polygons=polygons,
        district_street_segments=district_street_segments,
        heat_demand_file=HEAT_DEMAND_FILE,
        heating_shares_file=HEATING_SHARES_FILE,
        region_builder_config_overrides={"min_heat_grid_share": 0.20, "heat_grid_names": ("heat_exchanger",)},
    )
    es.data_dir = project_root / "data"
    es_plotter = EnergySystemPlotter(es)

    ###################
    # Optimizer block
    ###################
    scenario = build_scenario(
        model_name=MODEL_NAME,
        scenario_name=SCENARIO_NAME,
        tss_name=TSS_NAME,
        start_year=2020,
        end_year=2030,
        year_gap=5,
        retain_existing_output_drop_per_year=0.05,
        lockout_years=2,
    )
    backend = create_cesm_backend(project_root, model_name=MODEL_NAME, scenario_name=SCENARIO_NAME, tss_name=TSS_NAME, scenario=scenario, demand_name=DEMAND_NAME)
    solution = backend.optimize(es, scenario=scenario, demand_name=DEMAND_NAME)

    #################
    # Results block
    #################
    print(solution.results)
    years = years_for_scenario(scenario)
    plot_mix_results(es_plotter, solution.results, years, BENSHEIM_PLOTS_DIR)
    show_cesm_sankey(project_root, solution.results, years)


if __name__ == "__main__":
    main()
