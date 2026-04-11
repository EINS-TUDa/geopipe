from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from examples.run_models import DijkstraScenarioConfig, ScenarioCaseConfig, ScenarioRunOutput
from pypeline.runtime import (
    build_energy_system,
    build_scenario,
    create_cesm_backend,
)
from pypeline.optimization.cesm.input_writer import write_cesm_inputs_from_energy_system
from pypeline.optimization.cesm.reporting import write_cesm_results_html_report
from pypeline.plot.plotter import EnergySystemPlotter
from pypeline.topology_builder import DijkstraTopologyBuilder, DijkstraTopologyBuilderConfig, SimpleTopologyBuilder
from pypeline.topology_builder.analysis import edge_metrics_from_topology_result, streets_for_topology_plot


def render_topology_plot(run_output: Mapping[str, Any]) -> Path:
    if not isinstance(run_output, Mapping):
        raise TypeError("run_output must be a mapping")

    required_keys = (
        "plotter",
        "streets_with_region",
        "topology_plot_polygons",
        "region_id_column",
        "topology_plot_title",
        "topology_plot_filename",
        "plots_dir",
    )
    missing = [key for key in required_keys if key not in run_output]
    if missing:
        raise ValueError(f"run_output is missing required topology plot keys: {missing}")

    plotter = run_output["plotter"]
    streets_with_region = run_output["streets_with_region"]
    polygons = run_output["topology_plot_polygons"]
    region_id_column = str(run_output["region_id_column"])
    title = str(run_output["topology_plot_title"])
    filename = str(run_output["topology_plot_filename"])
    plots_dir = Path(run_output["plots_dir"])

    if not isinstance(plotter, EnergySystemPlotter):
        raise TypeError("run_output['plotter'] must be an EnergySystemPlotter")

    plots_dir.mkdir(parents=True, exist_ok=True)
    output_path = plots_dir / filename
    plotter.plot_streets_colored_by_region(
        streets_with_region=streets_with_region,
        polygons=polygons,
        output_path=output_path,
        region_id_column=region_id_column,
        title=title,
    )
    print(f"Saved topology plot: {output_path}")
    return output_path


def build_case_energy_system_from_scenario(
    *,
    scenario_file: Path,
    streets_file: Path,
    model_name: str,
    heat_demand_file: Path,
    heating_shares_file: Path,
    region_builder_config_overrides: dict[str, Any] | None,
    apply_injections: bool,
    project_root: Path,
):
    topology_result = SimpleTopologyBuilder(
        streets_file=streets_file,
        scenario_file=scenario_file,
        apply_injections=apply_injections,
    ).build()

    energy_system = build_energy_system(
        model_name=model_name,
        region_topologies=topology_result.region_topologies,
        street_network=topology_result.network,
        heat_demand_file=heat_demand_file,
        heating_shares_file=heating_shares_file,
        region_builder_config_overrides=region_builder_config_overrides,
        injected_techs=topology_result.injected_techs,
    )
    energy_system.data_dir = project_root / "data"
    return energy_system, topology_result


def _validate_case_regions(config: ScenarioCaseConfig, region_topologies: list[Any]) -> None:
    if config.expected_region_ids is None:
        return

    got = tuple(sorted(int(graph.graph["id"]) for graph in region_topologies))
    expected = tuple(sorted(int(rid) for rid in config.expected_region_ids))
    if got != expected:
        raise AssertionError(f"Unexpected region ids: got {list(got)}, expected {list(expected)}")


def _slug(text: str) -> str:
    return "_".join(str(text).strip().lower().split())


def _write_results_report(
    *,
    output_dir: Path,
    model_name: str,
    scenario_name: str,
    results_obj: Any,
    metadata: dict[str, Any],
) -> Path:
    filename = f"{_slug(model_name)}_{_slug(scenario_name)}_results.html"
    output_path = output_dir / filename
    return write_cesm_results_html_report(
        results_obj=results_obj,
        output_path=output_path,
        title=f"CESM Results - {model_name} / {scenario_name}",
        metadata=metadata,
    )


def _build_scenario_run_output(
    *,
    topology_result: Any,
    assigned_edges: int,
    demand_name: str,
    results_obj: Any,
    report_path: Path,
    scenario: Any,
    energy_system: Any,
    plots_dir: Path,
    project_root: Path,
    streets_for_plot: Any,
    model_name: str,
) -> ScenarioRunOutput:
    return ScenarioRunOutput(
        regions=int(len(topology_result.region_topologies)),
        network_edges=assigned_edges,
        network_edges_assigned=assigned_edges,
        injected_demand=float(topology_result.injected_demand_mwh),
        injected_tech_count=int(len(topology_result.injected_techs)),
        demand_name=demand_name,
        results_obj=results_obj,
        results_raw=results_obj.raw,
        results_report_html=report_path,
        years=scenario.years(),
        plotter=EnergySystemPlotter(energy_system),
        plots_dir=plots_dir,
        project_root=project_root,
        streets_with_region=streets_for_plot,
        topology_plot_polygons=EnergySystemPlotter.build_topology_plot_polygons_from_energy_system(
            energy_system=energy_system,
            demand_name=demand_name,
        ),
        region_id_column="id",
        topology_plot_title=f"District topology: {model_name}",
        topology_plot_filename=f"{str(model_name).strip().lower().replace(' ', '_')}_street_topology.png",
    )


def run_scenario_case_fast(config: ScenarioCaseConfig) -> dict[str, float | int]:
    energy_system, topology_result = build_case_energy_system_from_scenario(
        scenario_file=config.scenario_file,
        streets_file=config.streets_file,
        model_name=config.model_name,
        heat_demand_file=config.heat_demand_file,
        heating_shares_file=config.heating_shares_file,
        region_builder_config_overrides=config.region_builder_config_overrides,
        apply_injections=config.apply_injections,
        project_root=config.project_root,
    )

    _validate_case_regions(config, topology_result.region_topologies)
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")

    scenario = build_scenario(
        model_name=f"{config.model_name}FastCheck",
        scenario_name=f"{config.scenario_name}FastCheck",
        tss_name=config.tss_name,
        start_year=config.start_year,
        end_year=config.end_year,
        year_gap=config.year_gap,
        retain_existing_output_drop_per_year=config.retain_existing_output_drop_per_year,
        lockout_years=config.lockout_years,
    )

    write_cesm_inputs_from_energy_system(
        energy_system,
        scenario,
        workdir=config.project_root / "CESM",
        model_name=f"{config.model_name}FastCheck",
        scenario_name=f"{config.scenario_name}FastCheck",
        tss_name=config.tss_name,
        demand_name=config.demand_name,
    )

    return {
        "regions": int(len(topology_result.region_topologies)),
        "network_edges": assigned_edges,
        "network_edges_assigned": assigned_edges,
        "injected_demand": float(topology_result.injected_demand_mwh),
        "injected_tech_count": int(len(topology_result.injected_techs)),
    }


def run_scenario_case(config: ScenarioCaseConfig) -> ScenarioRunOutput:
    energy_system, topology_result = build_case_energy_system_from_scenario(
        scenario_file=config.scenario_file,
        streets_file=config.streets_file,
        model_name=config.model_name,
        heat_demand_file=config.heat_demand_file,
        heating_shares_file=config.heating_shares_file,
        region_builder_config_overrides=config.region_builder_config_overrides,
        apply_injections=config.apply_injections,
        project_root=config.project_root,
    )

    _validate_case_regions(config, topology_result.region_topologies)
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")
    streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    config.output_plots_dir.mkdir(parents=True, exist_ok=True)

    scenario = build_scenario(
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        tss_name=config.tss_name,
        start_year=config.start_year,
        end_year=config.end_year,
        year_gap=config.year_gap,
        retain_existing_output_drop_per_year=config.retain_existing_output_drop_per_year,
        lockout_years=config.lockout_years,
    )
    backend = create_cesm_backend(
        config.project_root,
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        tss_name=config.tss_name,
        scenario=scenario,
        demand_name=config.demand_name,
    )
    solution = backend.solve(energy_system, scenario=scenario, demand_name=config.demand_name)
    results_obj = solution.results
    report_path = _write_results_report(
        output_dir=config.output_plots_dir,
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
        },
    )

    return _build_scenario_run_output(
        topology_result=topology_result,
        assigned_edges=assigned_edges,
        demand_name=config.demand_name,
        results_obj=results_obj,
        report_path=report_path,
        scenario=scenario,
        energy_system=energy_system,
        plots_dir=config.output_plots_dir,
        project_root=config.project_root,
        streets_for_plot=streets_for_plot,
        model_name=config.model_name,
    )


def run_dijkstra_scenario(config: DijkstraScenarioConfig) -> ScenarioRunOutput:
    topology_config = DijkstraTopologyBuilderConfig(
        input_dir=config.input_dir,
        output_dir=config.output_dir,
        buildings_file=config.buildings_file,
        streets_file=config.streets_file,
        max_demand_mwh=config.max_demand_mwh,
        max_street_length_km=config.max_street_length_km,
        demand_share_pct=config.demand_share_pct,
        polynesia=config.polynesia,
        city_column=config.city_column,
    )

    topology_result = DijkstraTopologyBuilder(topology_config).build()
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")
    streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    energy_system = build_energy_system(
        model_name=config.model_name,
        region_topologies=topology_result.region_topologies,
        street_network=topology_result.network,
        heat_demand_file=config.heat_demand_file,
        heating_shares_file=config.heating_shares_file,
        region_builder_config_overrides=config.region_builder_config_overrides,
        injected_techs=None,
    )
    energy_system.data_dir = config.project_root / "data"

    config.output_plots_dir.mkdir(parents=True, exist_ok=True)

    scenario = build_scenario(
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        tss_name=config.tss_name,
        start_year=config.start_year,
        end_year=config.end_year,
        year_gap=config.year_gap,
        retain_existing_output_drop_per_year=config.retain_existing_output_drop_per_year,
        lockout_years=config.lockout_years,
    )
    backend = create_cesm_backend(
        config.project_root,
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        tss_name=config.tss_name,
        scenario=scenario,
        demand_name=config.demand_name,
    )
    solution = backend.solve(energy_system, scenario=scenario, demand_name=config.demand_name)
    results_obj = solution.results
    report_path = _write_results_report(
        output_dir=config.output_plots_dir,
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
        },
    )

    return _build_scenario_run_output(
        topology_result=topology_result,
        assigned_edges=assigned_edges,
        demand_name=config.demand_name,
        results_obj=results_obj,
        report_path=report_path,
        scenario=scenario,
        energy_system=energy_system,
        plots_dir=config.output_plots_dir,
        project_root=config.project_root,
        streets_for_plot=streets_for_plot,
        model_name=config.model_name,
    )
