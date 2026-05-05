import sqlite3
from pathlib import Path

from compare_techmaps import compare_techmaps
from examples.test_case_bensheim.input_data.data_reg import case1_data_registry
from pypeline.energy_system.energy_system import EnergySystemBuilder, EnergySystemBuilderConfig
from pypeline.energy_system import Scenario
from pypeline.energy_system import register_technologies
from pypeline.energy_system.units import UnitEnum
from pypeline.optimization import CESMOptimizationBackend, Solution
# from pypeline.plot.plotter import EnergySystemPlotter
from pypeline.topology_builder.topology_build_utils import modify_streets_data
from pypeline.topology_builder.topology_builder import SimpleTopologyBuilder
from cesm.core.plotter import Plotter as CesmPlotter, PlotType
from cesm.core.data_access import DAO

import logging

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logging.getLogger("pypeline").setLevel(logging.INFO)
logging.getLogger("cesm").setLevel(logging.INFO)

CASE_DIR = Path(__file__).resolve().parent
project_root = CASE_DIR.parents[1]

def main():
    streets_data = modify_streets_data(streets_data=CASE_DIR / "input_data" / "bensheim_streets_heat_demand.geojson",
                                       modifications_file=CASE_DIR / "input_data" / "modifications.yaml")

    topology_builder = SimpleTopologyBuilder()
    topology_builder.set_streets_data(streets_data)
    topology_builder.set_grouping(CASE_DIR / "input_data" / "region_grouping.yaml")
    topology_builder.set_region_id_column("id")
    topology_builder.set_extensive_columns(["waerme_mwh"])
    topology_result = topology_builder.build()

    esb_cfg = EnergySystemBuilderConfig(
        minimum_decentral_technology_share={"heat_exchanger": 0.1},
        considered_connected_region_distance_m=50,
        default_central_technology_per_commodity={"district_heat_in": "chp_gas"},
        preferred_central_technologies_location_per_commodity={"district_heat_in": [1]},
        additional_grid_capacity_factor={"heat_grid": 1}
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
    energy_system.plot_system_topology()
    scenario = Scenario(name=f"Base", start_year=2020, end_year=2030, year_gap=5, dt_hours=1, tss="8WeeksManual")
    backend = CESMOptimizationBackend(timeseries_dir=CASE_DIR / "input_data", output_dir=CASE_DIR / "output_data")
    solution = backend.solve(energy_system, scenario, lp_file=True)

    # solution.save(path=CASE_DIR / "output_data")
    # solution = Solution.load(path=CASE_DIR / "output_data", file_name="Case1_Base_Solution.pkl")
    compare_techmaps(CASE_DIR / "output_data" / "Case1_Base_old.xlsx", CASE_DIR / "output_data" / "Case1_Base.xlsx", path_output=CASE_DIR / "output_data" / "comparison.html")
    solution.write_html_report(output_path=CASE_DIR / "output_data" / f"Case1_Base_report.html")
    solution.plot_grid(grid_name="heat_grid", year=2030)
    solution.plot_decentral_shares(demand_name="residential_heat", year=scenario.years, metric="energy_output")

    # --- 3) Sankey diagrams via CESM plot module ---
    db_path = Path(solution.db_path)
    conn = sqlite3.connect(str(db_path))
    dao = DAO(conn)
    sankey_plotter = CesmPlotter(dao)
    for year in scenario.years:
        sankey_fig = sankey_plotter.plot_sankey(year=year)
        # sankey_output = CASE_DIR / "output_data" / f"sankey_{year}.html"
        # sankey_fig.write_html(str(sankey_output))
        # print(f"Saved Sankey diagram: {sankey_output}")

    # --- 4) Active capacity & new capacity plots for residential_heat_DXXX ---
    heat_commodities = [
        co for co in dao.get_set("commodity")
        if "residential_heat_D" in str(co)
    ]
    for commodity in heat_commodities:
        sankey_plotter.plot_bars(PlotType.Bar.ACTIVE_CAPACITY, commodity=commodity)
        sankey_plotter.plot_bars(PlotType.Bar.NEW_CAPACITY, commodity=commodity)
    conn.close()


if __name__ == '__main__':
    main()
