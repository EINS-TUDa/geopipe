import os
import sys
from pathlib import Path

from examples.example_runner import ScenarioCaseConfig, _validate_case_regions, _case_plots_dir, _write_results_report
from pypeline import TechnologyRegistry, EnergySystemBuilder, EnergySystemRuleBook
from pypeline.energy_system import Scenario
from pypeline.factory import create_local_data_registry
from pypeline.injection import apply_injected_techs
from pypeline.optimization import CESMOptimizationBackend
from pypeline.topology_builder.cli.simple import _resolve_case_folder, _resolve_case_file
from pypeline.topology_builder.core import edge_metrics_from_topology_result, streets_for_topology_plot
from pypeline.topology_builder.simple_builder import SimpleTopologyBuilderConfig, SimpleTopologyBuilder

project_root = Path(__file__).resolve().parents[2]
CASE_DIR = Path(__file__).resolve().parent

def main():
    config = ScenarioCaseConfig(
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
            "min_heat_grid_share": 0.1,
            "heat_grid_names": ("heat_exchanger",),
        },
        expected_region_ids=(0, 1),
    )

    case_folder = _resolve_case_folder(str("examples/test_cases"))
    resolved_streets = _resolve_case_file(case_folder, str(config.streets_file))
    resolved_scenario = _resolve_case_file(case_folder, str(config.scenario_file))

    if not resolved_streets.exists():
        raise FileNotFoundError(f"Streets file not found: {resolved_streets}")
    if not resolved_scenario.exists():
        raise FileNotFoundError(f"Scenario file not found: {resolved_scenario}")

    effective_apply_injections = config.apply_injections

    cfg = SimpleTopologyBuilderConfig(
        streets_file=resolved_streets,
        scenario_file=resolved_scenario,
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
    builder.set_region_topologies(topology_result.region_topologies)
    builder.set_street_network(topology_result.network)
    builder.set_demands(default=True)
    builder.set_technology_registry(tech_registry)
    builder.set_data_registry(create_local_data_registry(config.heat_demand_file, config.heating_shares_file))
    builder.set_default_region_builder_config()
    if config.region_builder_config_overrides:
        builder.set_region_builder_config(config.region_builder_config_overrides, merge=True)
    builder.set_energy_system_rule_book(EnergySystemRuleBook())

    energy_system = builder.build()
    apply_injected_techs(energy_system, topology_result.injected_techs or [])
    energy_system.data_dir = project_root / "data"

    scenario = Scenario(
        name=f"{config.model_name}-{config.scenario_name}",
        start_year=config.start_year,
        end_year=config.end_year,
        year_gap=config.year_gap,
        tss=config.tss_name,
        retain_existing_output_drop_per_year=config.retain_existing_output_drop_per_year,
        lockout_years=config.lockout_years,
    )

    _validate_case_regions(config, topology_result.region_topologies)
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")
    streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    plots_dir = _case_plots_dir(config)
    plots_dir.mkdir(parents=True, exist_ok=True)

    workdir = (project_root / "CESM").resolve()
    existing_py_path = os.environ.get("PYTHONPATH")
    if existing_py_path:
        os.environ["PYTHONPATH"] = f"{project_root}{os.pathsep}{existing_py_path}"
    else:
        os.environ["PYTHONPATH"] = str(project_root)

    backend = CESMOptimizationBackend(
        workdir=str(workdir),
        cli=[sys.executable, "-m", "pypeline.optimization.cesm.cli"],
        run_args=["--workdir", str(workdir), "-m", config.model_name, "-s", config.scenario_name],
        run_subdir=f"{config.model_name}-{config.scenario_name}",
        results_db_name="db.sqlite",
        write_inputs=True,
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        tss_name=config.tss_name,
        scenario=scenario,
        demand_name=config.demand_name,
    )

    solution = backend.solve(energy_system, scenario=scenario, demand_name=config.demand_name)
    results_obj = solution.results
    report_path = _write_results_report(
        output_dir=plots_dir,
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
            "apply_injections": effective_apply_injections,
        },
    )

main()