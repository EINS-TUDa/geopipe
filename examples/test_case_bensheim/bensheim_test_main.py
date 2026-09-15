from pathlib import Path

from geopipe.data import DataKeys, DataRegistryQuery
from geopipe.data.dataset import CensusTechnology
from geopipe.energy_system.demand import DemandType
from geopipe.energy_system.energy_system import EnergySystemBuilder, EnergySystemBuilderConfig
from geopipe.energy_system import Scenario
from geopipe.energy_system import register_technologies
from geopipe.energy_system.units import UnitEnum
from geopipe.optimization import CESMOptimizationBackend, Solution
from geopipe.topology_builder.topology_build_utils import modify_streets_data
from geopipe.topology_builder.topology_builder import PolygonTopologyBuilder
from geopipe.optimization.cesm import CesmPlotter, PlotType


from input_data.data_reg import case1_data_registry

import logging

CASE_DIR = Path(__file__).resolve().parent
project_root = CASE_DIR.parents[1]
(CASE_DIR / "output_data").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(filename=str(CASE_DIR / "output_data" / "output.log"), mode="w"),
        logging.StreamHandler()])

logging.getLogger("geopipe").setLevel(logging.INFO)
logging.getLogger("cesm").setLevel(logging.INFO)



def main():
    streets_data = modify_streets_data(streets_data=CASE_DIR / "private_data" / "bensheim_streets_heat_demand.geojson",
                                       modifications_file=CASE_DIR / "input_data" / "modifications.yaml")
    data_reg = case1_data_registry(streets_data)

    topology_builder = PolygonTopologyBuilder()
    topology_builder.set_data_registry(data_reg)
    topology_builder.set_polygons_data(CASE_DIR / "input_data" / "polygons.geojson", id_column="id")
    topology_builder.set_topology_connections_check(False)

    topology_result = topology_builder.build()

    esb_cfg = EnergySystemBuilderConfig(
        minimum_decentral_technology_share={"heat_exchanger": 0.1},
        considered_connected_region_distance_m=50,
        central_tech_locations_per_commodity={"district_heat_in": [0]},
        central_tech_existing_capacities={"district_heat_in": {0: [("cen_waste_heat_langnese", 0.6),("cen_gas_boiler", 0.4)],
                                                               "default": [("chp_gas", 0.8),("cen_gas_boiler", 0.2)]}},
        additional_grid_capacity_factor={"heat_grid": 1.1},
        forced_decentral_technology_share_per_region={2: {"heat_exchanger": 0.8,
                                                          "ind_oil_boiler": 0.2}},
    )

    register_technologies(CASE_DIR / "input_data" / "technologies_new.yaml", clear_registry=True)

    residential_heat_demand = DemandType(name="residential_heat",
                       commodity_in="residential_heat",
                       cooperation_of_technologies=False,
                       profile=DataRegistryQuery(key=DataKeys.RESIDENTIAL_HEAT_DEMAND_PROFILE),
                       value=DataRegistryQuery(key=DataKeys.RESIDENTIAL_HEAT_DEMAND),
                       technology_shares=DataRegistryQuery(
                           key=DataKeys.HEATING_SHARES,
                           params={"name_mapping": {
                               CensusTechnology.Gas: "ind_gas_boiler",
                               CensusTechnology.Oil: "ind_oil_boiler",
                               CensusTechnology.Wood: "ind_biomass",
                               CensusTechnology.Biomass: None,
                               CensusTechnology.Renewable: "ind_heat_pump",
                               CensusTechnology.Electric: None,
                               CensusTechnology.Coal: None,
                               CensusTechnology.District_Heating: "heat_exchanger",
                               CensusTechnology.NoEnergyCarrier: None,
                           }},
                       ),
                       default_decentral_supply_technology="ind_oil_boiler",
                       decrease_percent_per_year=0)

    pool_heat_demand = DemandType(name="pool_heat",
                       commodity_in="pool_heat",
                       cooperation_of_technologies=True,
                       profile=DataRegistryQuery(key="pool_heat_demand_profile"),
                       value=DataRegistryQuery(key="pool_heat_demand"),
                       technology_shares=None,
                       default_decentral_supply_technology=[("pool_heat_pump", 0.6),
                                                            ("pool_gas_boiler", 0.4)],
                       decrease_percent_per_year=0)

    builder = EnergySystemBuilder(energy_system_name="Case1")
    builder.set_system_topology(topology_result.network)
    builder.set_data_registry(data_reg)
    builder.set_config(esb_cfg)
    builder.set_demand_types([residential_heat_demand, pool_heat_demand])
    builder.set_imports_exports(CASE_DIR / "input_data" / "imports_exports.yaml")
    builder.set_unit(UnitEnum.KW)

    energy_system = builder.build()
    energy_system.plot_system_topology()
    scenario = Scenario(name=f"Base", start_year=2025, end_year=2045, year_gap=5, dt_hours=1, tss="8WeeksManual", co2_limit={2029: None, 2045: 0})
    backend = CESMOptimizationBackend(timeseries_dir=CASE_DIR / "input_data", output_dir=CASE_DIR / "output_data")
    solution = backend.solve(energy_system, scenario, mip_gap = 0.01, lp_file=False)

    solution.save(path=CASE_DIR / "output_data")
    # solution = Solution.load(path=CASE_DIR / "output_data", file_name="Case1_Base_Solution.pkl")
    solution.write_html_report(output_path=CASE_DIR / "output_data" / f"Case1_Base_report.html")
    solution.energy_system.plot_system_topology()
    solution.plot_grid(grid_name="heat_grid", year=2030, metric="energy_output")
    solution.plot_decentral_shares(demand_name="residential_heat", year=scenario.years, metric="energy_output")

    # --- 3) Sankey diagrams via CESM plot module ---
    # cesm_plotter = CesmPlotter(solution=solution)
    # for year in scenario.years:
    #     cesm_plotter.plot_sankey(year=year)
    #     for region_id in cesm_plotter.regions:
    #         cesm_plotter.plot_sankey(year=year, region=region_id)

    # --- 4) Active / new capacity for residential_heat ---
    # cesm_plotter.plot_bars(PlotType.Bar.ACTIVE_CAPACITY,
    #                        commodity="residential_heat",
    #                        region=CesmPlotter.ALL_REGIONS)
    # cesm_plotter.plot_bars(PlotType.Bar.NEW_CAPACITY,
    #                        commodity="residential_heat",
    #                        region=CesmPlotter.ALL_REGIONS)
    # for region_id in cesm_plotter.regions:
    #     cesm_plotter.plot_bars(PlotType.Bar.ACTIVE_CAPACITY,
    #                            commodity="residential_heat", region=region_id)
    #     cesm_plotter.plot_bars(PlotType.Bar.NEW_CAPACITY,
    #                            commodity="residential_heat", region=region_id)


if __name__ == '__main__':
    main()
