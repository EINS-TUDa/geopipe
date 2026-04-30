import sqlite3
from pathlib import Path

from examples.test_case_bensheim.input_data.data_reg import case1_data_registry
# from examples.example_runner import ScenarioCaseConfig, #_validate_case_regions, _case_plots_dir, write_results_report
from pypeline.energy_system.energy_system import EnergySystemBuilder, EnergySystem, EnergySystemBuilderConfig
from pypeline.energy_system import Scenario
from pypeline.energy_system import register_technologies
from pypeline.energy_system.units import UnitEnum
from pypeline.optimization import CESMOptimizationBackend
# from pypeline.injection import apply_injected_techs
# from pypeline.optimization import CESMOptimizationBackend
# from pypeline.plot.plotter import EnergySystemPlotter
# from pypeline.topology_builder.core import edge_metrics_from_topology_result, streets_for_topology_plot
from pypeline.topology_builder.simple_builder import SimpleTopologyBuilderConfig, SimpleTopologyBuilder
# from cesm.core.plotter import Plotter as CesmPlotter, PlotType
# from cesm.core.data_access import DAO

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logging.getLogger("gurobipy").setLevel(logging.WARNING)

CASE_DIR = Path(__file__).resolve().parent
project_root = CASE_DIR.parents[1]

def main():
    cfg = SimpleTopologyBuilderConfig(
        streets_file=CASE_DIR / "input_data" / "bensheim_streets_heat_demand.geojson",
        scenario_file=CASE_DIR / "case_1.yaml",
        region_id_column="id",
        street_id_column="street_id",
        demand_column="waerme_mwh",
        street_length_column="laenge_segment",
        apply_injections=True,
    )
    topology_result = SimpleTopologyBuilder.from_config(cfg).build()

    esb_cfg = EnergySystemBuilderConfig(
        minimum_decentral_technology_share={"heat_exchanger": 0.1},
        considered_connected_region_distance_m= 50,
        default_central_technology_per_commodity={"district_heat_in": "cen_gas_boiler"},
        preferred_central_technologies_location_per_commodity={"district_heat_in": [1]},
        additional_grid_capacity_factor={"heat_grid" : 1.5}
    )

    data_reg = case1_data_registry()
    register_technologies(CASE_DIR / "input_data" / "technologies_new.yaml", clear_registry=True)

    builder = EnergySystemBuilder(energy_system_name="Case1")
    builder.set_system_topology(topology_result.network)
    builder.set_data_registry(data_reg)
    builder.set_config(esb_cfg)
    builder.set_imports(CASE_DIR / "input_data" / "imports.yaml")
    builder.set_unit(UnitEnum.KW)

    energy_system = builder.build()
    scenario = Scenario(name=f"Base", start_year=2020, end_year=2030, year_gap=5, dt_hours=3, tss="4ThinWeeks")

    # streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    backend = CESMOptimizationBackend(timeseries_dir=CASE_DIR / "input_data", output_dir=CASE_DIR / "output_data")

    solution = backend.solve(energy_system, scenario=scenario)
    # results_obj = solution.results
    # write_results_report(
    #     output_dir=CASE_DIR / "output_data",
    #     model_name=config.model_name,
    #     scenario_name=config.scenario_name,
    #     results_obj=results_obj,
    #     metadata={
    #         "model_name": config.model_name,
    #         "scenario_name": config.scenario_name,
    #         "tss_name": config.tss_name,
    #         "demand_name": config.demand_name,
    #         "start_year": config.start_year,
    #         "end_year": config.end_year,
    #         "year_gap": config.year_gap,
    #         "apply_injections": config.apply_injections,
    #     },
    # )
    #
    # from compare_techmaps import compare_techmaps
    # compare_techmaps(path_v1=CASE_DIR / "output_data" / f"Case1_pre_refactor.xlsx", path_v2=CASE_DIR / "output_data" / f"Case1.xlsx", path_output=CASE_DIR / "output_data" / "techmap_comparison.html")

    # # --- 1) Street topology plot ---
    # topology_polygons = EnergySystemPlotter.build_topology_plot_polygons_from_energy_system(
    #     energy_system=energy_system,
    #     demand_name=config.demand_name,
    # )
    # topology_plot_path = plots_dir / "case1_street_topology.png"
    # EnergySystemPlotter.plot_streets_colored_by_region(
    #     streets_with_region=streets_for_plot,
    #     polygons=topology_polygons,
    #     output_path=topology_plot_path,
    #     region_id_column="id",
    #     title=f"District topology: {config.model_name}",
    # )
    # print(f"Saved topology plot: {topology_plot_path}")
    #
    # # --- 2) Technology mix plot ---
    # plotter = EnergySystemPlotter(energy_system)
    # years = scenario.years
    # mix_plot_paths = plotter.save_default_mix_plots(
    #     results_obj.raw,
    #     years=years,
    #     plots_dir=plots_dir,
    #     demand_name=config.demand_name,
    # )
    # print(f"Saved technology mix plot: {mix_plot_paths['technology']}")
    #
    # # --- 3) Sankey diagrams via CESM plot module ---
    # db_path = Path(results_obj.raw["db"])
    # conn = sqlite3.connect(str(db_path))
    # try:
    #     dao = DAO(conn)
    #     sankey_plotter = CesmPlotter(dao)
    #     for year in years:
    #         sankey_fig = sankey_plotter.plot_sankey(year=year)
    #         sankey_output = plots_dir / f"sankey_{year}.html"
    #         sankey_fig.write_html(str(sankey_output))
    #         print(f"Saved Sankey diagram: {sankey_output}")
    #
    #     # --- 4) Active capacity & new capacity plots for residential_heat_DXXX ---
    #     heat_commodities = [
    #         co for co in dao.get_set("commodity")
    #         if "residential_heat_D" in str(co)
    #     ]
    #     for commodity in heat_commodities:
    #         sankey_plotter.plot_bars(PlotType.Bar.ACTIVE_CAPACITY, commodity=commodity)
    #         sankey_plotter.plot_bars(PlotType.Bar.NEW_CAPACITY, commodity=commodity)
    # finally:
    #     conn.close()


main()