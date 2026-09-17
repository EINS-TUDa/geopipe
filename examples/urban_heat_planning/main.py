from pathlib import Path
import geopandas as gpd

from geopipe.data import DataRegistryQuery, DataKeys
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

from input_data.data_reg import example_data_registry

import logging

CASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = CASE_DIR / "output_data"
INPUT_DIR = CASE_DIR / "input_data"
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(filename=str(OUTPUT_DIR / "output.log"), mode="w"),
              logging.StreamHandler()])
logging.getLogger("geopipe").setLevel(logging.INFO)
logging.getLogger("cesm").setLevel(logging.INFO)


def main():
    streets_data = gpd.read_file(INPUT_DIR / "WaermeatlasHessen.gpkg", layer="WAH_Strassenabschnitte")
    streets_data = modify_streets_data(streets_data=streets_data,
                                       modifications_file=INPUT_DIR / "update_heat_demand.yaml")
    data_reg = example_data_registry(streets_data)
    register_technologies(INPUT_DIR / "technologies.yaml", clear_registry=True)

    topology_builder = PolygonTopologyBuilder()
    topology_builder.set_data_registry(data_registry=data_reg)
    topology_builder.set_polygons_data(INPUT_DIR / "polygons.geojson", id_column="id")
    topology_builder.set_topology_connections_check(False)
    topology_result = topology_builder.build()

    esb_cfg = EnergySystemBuilderConfig(
        minimum_decentral_technology_share={"heat_exchanger": 0.4},
        considered_connected_region_distance_m=50,
        central_tech_locations_per_commodity={"district_heat_in": [1, 2, 4, 6, 7, 8, 11]},
        central_tech_existing_capacities={"district_heat_in": {"default": [("chp_gas", 0.8), ("cen_gas_boiler", 0.2)]}},
        additional_grid_capacity_factor={"heat_grid": 1.1},
        forced_decentral_technology_share_per_region={1: {"heat_exchanger": 0.5, "ind_gas_boiler": 0.5},
                                                      6: {"heat_exchanger": 1},
                                                      4: {"heat_exchanger": 0.8}
                                                      })

    residential_heat_demand = DemandType(name="residential_heat",
                                         commodity_in="residential_heat",
                                         cooperation_of_technologies=False,
                                         profile=DataRegistryQuery(key=DataKeys.RESIDENTIAL_HEAT_DEMAND_PROFILE),
                                         value=DataRegistryQuery(key=DataKeys.RESIDENTIAL_HEAT_DEMAND),
                                         technology_shares=DataRegistryQuery(key=DataKeys.HEATING_SHARES,
                                                                             params={"name_mapping": {
                                                                                 CensusTechnology.Gas: "ind_gas_boiler",
                                                                                 CensusTechnology.Oil: "ind_oil_boiler",
                                                                                 CensusTechnology.Wood: "ind_biomass",
                                                                                 CensusTechnology.Biomass: "ind_biomass",
                                                                                 CensusTechnology.Renewable: "ind_heat_pump",
                                                                                 CensusTechnology.Electric: "dec_direct_electric",
                                                                                 CensusTechnology.Coal: None,
                                                                                 CensusTechnology.District_Heating: "heat_exchanger",
                                                                                 CensusTechnology.NoEnergyCarrier: None,
                                                                             }},
                                                                             ),
                                         default_decentral_supply_technology="ind_gas_boiler",
                                         decrease_percent_per_year=0)

    builder = EnergySystemBuilder(energy_system_name="Bensheim")
    builder.set_system_topology(topology_result.network)
    builder.set_data_registry(data_reg)
    builder.set_config(esb_cfg)
    builder.set_demand_types([residential_heat_demand])
    builder.set_imports_exports(INPUT_DIR / "commodity_import.yaml")
    builder.set_unit(UnitEnum.KW)

    energy_system = builder.build()
    energy_system.plot_system_topology(output_path=OUTPUT_DIR / "topology.png", show=False)

    scenario = Scenario(name=f"45-8weeks", start_year=2025, end_year=2045, year_gap=5, dt_hours=1,
                        tss="8WeeksManual", co2_limit={2025: 47500, 2045: 0},
                        discount_rate=0.02)

    backend = CESMOptimizationBackend(timeseries_dir=INPUT_DIR, output_dir=OUTPUT_DIR)
    solution = backend.solve(energy_system, scenario, mip_gap=0.02, lp_file=False)
    solution.save(path=OUTPUT_DIR)

    solution.write_html_report(output_path=OUTPUT_DIR / f"Bensheim_report.html")
    solution.energy_system.plot_system_topology(output_path=OUTPUT_DIR / "topology_solved.png", show=False)
    solution.plot_grid(grid_name="heat_grid", year=2030, metric="energy_output",
                       output_path=OUTPUT_DIR / "heat_grid_2030_energy_output.png", show=False)
    solution.plot_decentral_shares(demand_name="residential_heat", year=scenario.years, metric="energy_output",
                                   output_path=OUTPUT_DIR / "decentral_shares_residential_heat.png", show=False)

    # sankey_dir = RUN_OUTPUT_DIR / "sankey"
    # sankey_dir.mkdir(parents=True, exist_ok=True)
    # cesm_plotter = CesmPlotter(solution=solution, show=False)
    # for year in scenario.years:
    #     cesm_plotter.plot_sankey(year=year).write_html(sankey_dir / f"sankey_{year}_all_regions.html")
    #     for region_id in cesm_plotter.regions:
    #         cesm_plotter.plot_sankey(year=year, region=region_id).write_html(
    #             sankey_dir / f"sankey_{year}_region{region_id}.html")
    #
    # bars_dir = RUN_OUTPUT_DIR / "bars"
    # bars_dir.mkdir(parents=True, exist_ok=True)
    # cesm_plotter.plot_bars(PlotType.Bar.ACTIVE_CAPACITY,
    #                        commodity="residential_heat",
    #                        region=CesmPlotter.ALL_REGIONS).write_html(
    #     bars_dir / "active_capacity_residential_heat_all_regions.html")
    # cesm_plotter.plot_bars(PlotType.Bar.NEW_CAPACITY,
    #                        commodity="residential_heat",
    #                        region=CesmPlotter.ALL_REGIONS).write_html(
    #     bars_dir / "new_capacity_residential_heat_all_regions.html")
    # for region_id in cesm_plotter.regions:
    #     cesm_plotter.plot_bars(PlotType.Bar.ACTIVE_CAPACITY,
    #                            commodity="residential_heat", region=region_id).write_html(
    #         bars_dir / f"active_capacity_residential_heat_region{region_id}.html")
    #     cesm_plotter.plot_bars(PlotType.Bar.NEW_CAPACITY,
    #                            commodity="residential_heat", region=region_id).write_html(
    #         bars_dir / f"new_capacity_residential_heat_region{region_id}.html")
    #


if __name__ == '__main__':
    main()
