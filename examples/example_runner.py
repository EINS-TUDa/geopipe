from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypeline.optimization.cesm.reporting import write_cesm_results_html_report
from pypeline.plot.plotter import EnergySystemPlotter
from pypeline.factory import (
    build_energy_system,
    build_scenario,
    create_cesm_backend,
)
from pypeline.topology_builder.cli.simple import build_topology_from_case
from pypeline.topology_builder.cli.dijkstra import build_topology_from_dataset
from pypeline.topology_builder.core import (
    edge_metrics_from_topology_result,
    streets_for_topology_plot,
)


@dataclass(frozen=True)
class ScenarioCaseConfig:
    project_root: Path
    scenario_file: Path
    streets_file: Path
    heating_shares_file: Path
    model_name: str
    scenario_name: str
    tss_name: str
    dt_hours: int
    demand_name: str
    start_year: int
    end_year: int
    year_gap: int
    retain_existing_output_drop_per_year: float
    lockout_years: int
    region_builder_config_overrides: dict[str, Any] | None = None
    expected_region_ids: tuple[int, ...] | None = None
    apply_injections: bool = False


@dataclass(frozen=True)
class DijkstraScenarioConfig:
    project_root: Path
    input_dir: Path
    output_dir: Path
    streets_file: str
    heating_shares_file: Path
    model_name: str
    scenario_name: str
    tss_name: str
    dt_hours: int
    demand_name: str
    start_year: int
    end_year: int
    year_gap: int
    retain_existing_output_drop_per_year: float
    lockout_years: int
    max_demand_mwh: float
    max_street_length_km: float
    demand_share_pct: float
    polynesia: bool
    city_column: str
    region_builder_config_overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScenarioExecutionResult:
    regions: int
    network_edges: int
    network_edges_assigned: int
    injected_demand: float
    injected_tech_count: int
    demand_name: str
    results_raw: dict[str, Any] | Any
    results_report_html: Path | None
    years: list[int]
    plotter: EnergySystemPlotter
    plots_dir: Path
    project_root: Path
    streets_with_region: Any
    topology_plot_polygons: Any
    region_id_column: str
    topology_plot_title: str
    topology_plot_filename: str
    results_obj: Any = None


def build_case_energy_system_from_scenario(
    *,
    scenario_file: Path,
    streets_file: Path,
    model_name: str,
    heating_shares_file: Path,
    region_builder_config_overrides: dict[str, Any] | None,
    apply_injections: bool,
    project_root: Path,
):
    topology_result = build_topology_from_case(
        case=scenario_file.parent,
        streets_file=str(streets_file),
        scenario_file=str(scenario_file),
        apply_injections=apply_injections,
    )

    energy_system = build_energy_system(
        model_name=model_name,
        region_topologies=topology_result.region_topologies,
        street_network=topology_result.network,
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


def _case_plots_dir(config: ScenarioCaseConfig) -> Path:
    return config.scenario_file.parent / "output_data" / "plots"


def _dijkstra_plots_dir(config: DijkstraScenarioConfig) -> Path:
    return config.output_dir / "plots"


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


def _build_execution_result(
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
) -> ScenarioExecutionResult:
    return ScenarioExecutionResult(
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


def run_scenario_case(
    config: ScenarioCaseConfig,
    *,
    apply_injections: bool | None = None,
) -> ScenarioExecutionResult:
    effective_apply_injections = config.apply_injections if apply_injections is None else bool(apply_injections)

    energy_system, topology_result = build_case_energy_system_from_scenario(
        scenario_file=config.scenario_file,
        streets_file=config.streets_file,
        model_name=config.model_name,
        heating_shares_file=config.heating_shares_file,
        region_builder_config_overrides=config.region_builder_config_overrides,
        apply_injections=effective_apply_injections,
        project_root=config.project_root,
    )

    _validate_case_regions(config, topology_result.region_topologies)
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")
    streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    plots_dir = _case_plots_dir(config)
    plots_dir.mkdir(parents=True, exist_ok=True)

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
        dt_hours=config.dt_hours,
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
            "dt_hours": config.dt_hours,
            "demand_name": config.demand_name,
            "start_year": config.start_year,
            "end_year": config.end_year,
            "year_gap": config.year_gap,
            "apply_injections": effective_apply_injections,
        },
    )

    return _build_execution_result(
        topology_result=topology_result,
        assigned_edges=assigned_edges,
        demand_name=config.demand_name,
        results_obj=results_obj,
        report_path=report_path,
        scenario=scenario,
        energy_system=energy_system,
        plots_dir=plots_dir,
        project_root=config.project_root,
        streets_for_plot=streets_for_plot,
        model_name=config.model_name,
    )


def run_dijkstra_scenario(config: DijkstraScenarioConfig) -> ScenarioExecutionResult:
    topology_result = build_topology_from_dataset(
        input_dir=config.input_dir,
        output_dir=config.output_dir,
        streets_file=config.streets_file,
        max_demand_mwh=config.max_demand_mwh,
        max_street_length_km=config.max_street_length_km,
        demand_share_pct=config.demand_share_pct,
        polynesia=config.polynesia,
        city_column=config.city_column,
    )
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")
    streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    energy_system = build_energy_system(
        model_name=config.model_name,
        region_topologies=topology_result.region_topologies,
        street_network=topology_result.network,
        heating_shares_file=config.heating_shares_file,
        region_builder_config_overrides=config.region_builder_config_overrides,
        injected_techs=None,
    )
    energy_system.data_dir = config.project_root / "data"

    plots_dir = _dijkstra_plots_dir(config)
    plots_dir.mkdir(parents=True, exist_ok=True)

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
        dt_hours=config.dt_hours,
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
            "dt_hours": config.dt_hours,
            "demand_name": config.demand_name,
            "start_year": config.start_year,
            "end_year": config.end_year,
            "year_gap": config.year_gap,
        },
    )

    return _build_execution_result(
        topology_result=topology_result,
        assigned_edges=assigned_edges,
        demand_name=config.demand_name,
        results_obj=results_obj,
        report_path=report_path,
        scenario=scenario,
        energy_system=energy_system,
        plots_dir=plots_dir,
        project_root=config.project_root,
        streets_for_plot=streets_for_plot,
        model_name=config.model_name,
    )
