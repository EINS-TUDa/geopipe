import sqlite3
from pathlib import Path

from examples.example_runner import ScenarioCaseConfig, _validate_case_regions, _case_plots_dir, write_results_report
from pypeline import TechnologyRegistry, EnergySystemBuilder, EnergySystemRuleBook
from pypeline.energy_system import Scenario
from pypeline.factory import create_local_data_registry
from pypeline.injection import apply_injected_techs
from pypeline.optimization import CESMOptimizationBackend
from pypeline.plot.plotter import EnergySystemPlotter
from pypeline.topology_builder.core import edge_metrics_from_topology_result, streets_for_topology_plot
from pypeline.topology_builder.simple_builder import SimpleTopologyBuilderConfig, SimpleTopologyBuilder
from cesm.core.plotter import Plotter as CesmPlotter, PlotType
from cesm.core.data_access import DAO

CASE_DIR = Path(__file__).resolve().parent
project_root = CASE_DIR.parents[1]

def main():
    config = ScenarioCaseConfig(
        project_root=project_root,
        scenario_file=CASE_DIR / "case_1.yaml",
        streets_file=CASE_DIR / "input_data" / "linear_heat_density.geojson",
        heating_shares_file=CASE_DIR / "input_data" / "heating_shares_neuburg.geojson",
        model_name="Case1",
        scenario_name="BaseCase1",
        tss_name="4ThinWeeks",
        dt_hours=3,
        demand_name="residential_heat",
        start_year=2020,
        end_year=2030,
        year_gap=5,
        retain_existing_output_drop_per_year=0.05,
        lockout_years=2,
        apply_injections=True,
        region_builder_config_overrides={
            "min_heat_grid_share": 0.1,
            "heat_grid_names": ("heat_exchanger",),
        },
        expected_region_ids=(0, 1, 2),
    )

    cfg = SimpleTopologyBuilderConfig(
        streets_file=config.streets_file,
        scenario_file=config.scenario_file,
        region_id_column="id",
        street_id_column="street_id",
        demand_column="total_heat_demand",
        street_length_column="street_length",
        apply_injections=True,
    )
    topology_result = SimpleTopologyBuilder.from_config(cfg).build()

    tech_registry = TechnologyRegistry()
    tech_registry.load_from_default()

    builder = EnergySystemBuilder(energy_system_name=config.model_name)
    builder.set_street_network(topology_result.network)
    builder.set_demands(default=True)
    builder.set_technology_registry(tech_registry)
    builder.set_data_registry(create_local_data_registry(config.heating_shares_file))
    builder.set_imports(import_yaml=CASE_DIR / "input_data" / "imports.yaml")
    builder.set_energy_system_rule_book(EnergySystemRuleBook())

    builder.set_default_region_builder_config()
    if config.region_builder_config_overrides:
        builder.set_region_builder_config(config.region_builder_config_overrides, merge=True)

    energy_system = builder.build()
    apply_injected_techs(energy_system, topology_result.injected_techs or [])
    energy_system.data_dir = project_root / "data"

    scenario = Scenario(
        name=f"{config.scenario_name}",
        start_year=config.start_year,
        end_year=config.end_year,
        year_gap=config.year_gap,
        dt_hours=config.dt_hours,
        tss=config.tss_name,
        retain_existing_output_drop_per_year=config.retain_existing_output_drop_per_year,
        lockout_years=config.lockout_years,
    )

    _validate_case_regions(config, topology_result.region_topologies)
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")
    streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    plots_dir = _case_plots_dir(config)
    plots_dir.mkdir(parents=True, exist_ok=True)

    backend = CESMOptimizationBackend(
        timeseries_dir=CASE_DIR / "input_data",
        output_dir=CASE_DIR / "output_data",
        results_db_name="db.sqlite",
        demand_name=config.demand_name,
    )

    solution = backend.solve(energy_system, scenario=scenario)
    results_obj = solution.results
    write_results_report(
        output_dir=CASE_DIR / "output_data",
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        results_obj=results_obj,
        metadata={
            "model_name": config.model_name,
            "scenario_name": config.scenario_name,
            "tss_name": config.tss_name,
            "demand_name": config.demand_name,
            "start_year": config.start_year,
            "end_year": config.end_year,
            "year_gap": config.year_gap,
            "apply_injections": config.apply_injections,
        },
    )

    from compare_techmaps import compare_techmaps
    compare_techmaps(path_v1=CASE_DIR / "output_data" / f"Case1_pre_refactor.xlsx", path_v2=CASE_DIR / "output_data" / f"Case1.xlsx", path_output=CASE_DIR / "output_data" / "techmap_comparison.html")

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