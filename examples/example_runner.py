"""Orchestrates end-to-end any example case by building topology and energy systems, 
executing CESM optimization, and producing plots and reports."""

from __future__ import annotations
from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
from typing import Any
from pypeline.energy_system_my.scenario import Scenario
from pypeline.optimization.cesm.reporting import write_cesm_results_html_report
from pypeline.optimization import CESMOptimizationBackend
from pypeline.plot.plotter import EnergySystemPlotter
from pypeline.factory import build_energy_system
from pypeline.topology_builder.cli.simple import build_topology_from_case
from pypeline.topology_builder.cli.dijkstra import build_topology_from_dataset
from pypeline.topology_builder.core import (edge_metrics_from_topology_result,streets_for_topology_plot)

logger = logging.getLogger(__name__)


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
    commodity_activation_year_by_name: dict[str, int] | None = None
    technology_activation_year_by_name: dict[str, int] | None = None
    constraints_overrides: dict[str, Any] | None = None
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
    commodity_activation_year_by_name: dict[str, int] | None = None
    technology_activation_year_by_name: dict[str, int] | None = None
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


class _CliScenario(Scenario):
    @property
    def lockout_until_year(self) -> int:
        return int(self.start_year) + max(0, int(self.lockout_years))


def _build_cli_scenario(
    *,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    dt_hours: int,
    start_year: int,
    end_year: int,
    year_gap: int,
    retain_existing_output_drop_per_year: float,
    lockout_years: int,
) -> Scenario:
    return _CliScenario(
        name=f"{model_name}-{scenario_name}",
        start_year=start_year,
        end_year=end_year,
        year_gap=year_gap,
        dt_hours=dt_hours,
        tss=tss_name,
        retain_existing_output_drop_per_year=retain_existing_output_drop_per_year,
        lockout_years=lockout_years,
    )


def build_case_energy_system_from_scenario(
    *,
    scenario_file: Path,
    streets_file: Path,
    model_name: str,
    heating_shares_file: Path,
    region_builder_config_overrides: dict[str, Any] | None,
    commodity_activation_year_by_name: dict[str, int] | None,
    technology_activation_year_by_name: dict[str, int] | None,
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
        street_network=topology_result.network,
        heating_shares_file=heating_shares_file,
        region_builder_config_overrides=region_builder_config_overrides,
        injected_techs=topology_result.injected_techs,
        commodity_activation_year_by_name=commodity_activation_year_by_name,
        technology_activation_year_by_name=technology_activation_year_by_name,
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


def _ensure_tss_seed_file(*, timeseries_dir: Path, tss_name: str, project_root: Path) -> None:
    target = timeseries_dir / f"{tss_name}.txt"
    if target.exists():
        return

    candidates = [
        project_root / "examples" / "test_cases" / "input_data" / f"{tss_name}.txt",
        project_root / "Data" / "TimeSeries" / f"{tss_name}.txt",
        project_root / "data" / f"{tss_name}.txt",
    ]
    source = next((path for path in candidates if path.exists()), None)
    if source is None:
        raise FileNotFoundError(
            f"Missing TSS seed file for '{tss_name}'. Expected one of: {candidates}"
        )

    shutil.copyfile(source, target)
    logger.info("Copied TSS file %s -> %s", source, target)


def _derive_heat_demand_profile_from_energy_system(
    energy_system: Any,
    *,
    demand_name: str,
) -> list[float]:
    demand_key = str(demand_name).strip().lower()
    profiles: list[tuple[list[float], float]] = []

    for region in getattr(energy_system, "regions", []):
        for region_demand in getattr(region, "region_demands", []):
            demand = getattr(region_demand, "demand", None)
            demand_type = str(getattr(demand, "demand_type", "") or "").strip().lower()
            commodity_in = str(getattr(demand, "commodity_in", "") or "").strip().lower()
            if demand_key not in {demand_type, commodity_in}:
                continue

            profile_raw = getattr(region_demand, "profile", None)
            if profile_raw is None:
                continue
            if hasattr(profile_raw, "tolist"):
                profile_raw = profile_raw.tolist()

            profile = [float(value) for value in profile_raw]
            if len(profile) != 8760:
                continue

            annual_weight = max(0.0, float(getattr(region_demand, "value", 0.0) or 0.0))
            profiles.append((profile, annual_weight))

    if not profiles:
        raise ValueError(
            "Could not derive HeatDemandProfile from region demands; "
            f"no 8760 profile found for demand '{demand_name}'."
        )

    weighted = [(profile, weight) for profile, weight in profiles if weight > 0.0]
    if not weighted:
        weighted = [(profile, 1.0) for profile, _ in profiles]

    aggregate = [0.0] * 8760
    total_weight = sum(weight for _, weight in weighted)
    for profile, weight in weighted:
        factor = float(weight) / float(total_weight)
        for idx, value in enumerate(profile):
            aggregate[idx] += factor * float(value)

    profile_sum = float(sum(aggregate))
    if profile_sum <= 0.0:
        raise ValueError("Derived HeatDemandProfile has non-positive sum")
    return [value / profile_sum for value in aggregate]


def _write_heat_demand_profile_file(*, profile: list[float], timeseries_dir: Path) -> None:
    output_path = timeseries_dir / "HeatDemandProfile.txt"
    output_path.write_text(" ".join(f"{value:.8f}" for value in profile), encoding="utf-8")
    logger.info("Wrote demand profile: %s", output_path)


def _ensure_cli_timeseries_inputs(
    *,
    energy_system: Any,
    demand_name: str,
    tss_name: str,
    timeseries_dir: Path,
    project_root: Path,
) -> None:
    timeseries_dir.mkdir(parents=True, exist_ok=True)
    _ensure_tss_seed_file(timeseries_dir=timeseries_dir, tss_name=tss_name, project_root=project_root)
    profile = _derive_heat_demand_profile_from_energy_system(energy_system, demand_name=demand_name)
    _write_heat_demand_profile_file(profile=profile, timeseries_dir=timeseries_dir)


def write_results_report(
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
        years=scenario.years,
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

    commodity_activation_year_by_name: dict[str, int] = {
        str(name).strip().lower(): int(year)
        for name, year in (config.commodity_activation_year_by_name or {}).items()
        if str(name).strip()
    }
    technology_activation_year_by_name: dict[str, int] = {
        str(name).strip().lower(): int(year)
        for name, year in (config.technology_activation_year_by_name or {}).items()
        if str(name).strip()
    }
    if config.constraints_overrides:
        legacy_map = config.constraints_overrides.get("commodity_activation_year")
        if legacy_map is not None and not isinstance(legacy_map, dict):
            raise ValueError("constraints_overrides['commodity_activation_year'] must be a mapping")
        for name, year in (legacy_map or {}).items():
            key = str(name).strip().lower()
            if not key:
                continue
            commodity_activation_year_by_name[key] = int(year)

        if "hydrogen_start_year" in config.constraints_overrides:
            commodity_activation_year_by_name["hydrogen"] = int(config.constraints_overrides["hydrogen_start_year"])

        tech_map = config.constraints_overrides.get("technology_activation_year")
        if tech_map is not None and not isinstance(tech_map, dict):
            raise ValueError("constraints_overrides['technology_activation_year'] must be a mapping")
        for name, year in (tech_map or {}).items():
            key = str(name).strip().lower()
            if not key:
                continue
            technology_activation_year_by_name[key] = int(year)

    energy_system, topology_result = build_case_energy_system_from_scenario(
        scenario_file=config.scenario_file,
        streets_file=config.streets_file,
        model_name=config.model_name,
        heating_shares_file=config.heating_shares_file,
        region_builder_config_overrides=config.region_builder_config_overrides,
        commodity_activation_year_by_name=commodity_activation_year_by_name or None,
        technology_activation_year_by_name=technology_activation_year_by_name or None,
        apply_injections=effective_apply_injections,
        project_root=config.project_root,
    )

    if config.constraints_overrides:
        merged_constraints = dict(energy_system.constraints or {})
        for key, value in config.constraints_overrides.items():
            if key in {"commodity_activation_year", "technology_activation_year", "hydrogen_start_year"}:
                continue
            if isinstance(value, dict) and isinstance(merged_constraints.get(key), dict):
                merged_constraints[key] = {**merged_constraints[key], **value}
            else:
                merged_constraints[key] = value
        energy_system.constraints = merged_constraints

    _validate_case_regions(config, topology_result.region_topologies)
    _, assigned_edges = edge_metrics_from_topology_result(topology_result, region_id_column="id")
    streets_for_plot = streets_for_topology_plot(topology_result, region_id_column="id")

    plots_dir = _case_plots_dir(config)
    plots_dir.mkdir(parents=True, exist_ok=True)

    scenario = _build_cli_scenario(
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        tss_name=config.tss_name,
        dt_hours=config.dt_hours,
        start_year=config.start_year,
        end_year=config.end_year,
        year_gap=config.year_gap,
        retain_existing_output_drop_per_year=config.retain_existing_output_drop_per_year,
        lockout_years=config.lockout_years,
    )
    case_dir = config.scenario_file.parent
    _ensure_cli_timeseries_inputs(
        energy_system=energy_system,
        demand_name=config.demand_name,
        tss_name=config.tss_name,
        timeseries_dir=case_dir / "input_data",
        project_root=config.project_root,
    )
    backend = CESMOptimizationBackend(
        timeseries_dir=case_dir / "input_data",
        output_dir=case_dir / "output_data",
        results_db_name="db.sqlite",
        demand_name=config.demand_name,
        retain_existing_output_schedule=scenario.retain_existing_output_schedule,
    )
    solution = backend.solve(energy_system, scenario=scenario)
    results_obj = solution.results
    report_path = write_results_report(
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
        street_network=topology_result.network,
        heating_shares_file=config.heating_shares_file,
        region_builder_config_overrides=config.region_builder_config_overrides,
        injected_techs=None,
        commodity_activation_year_by_name=config.commodity_activation_year_by_name,
        technology_activation_year_by_name=config.technology_activation_year_by_name,
    )
    energy_system.data_dir = config.project_root / "data"

    plots_dir = _dijkstra_plots_dir(config)
    plots_dir.mkdir(parents=True, exist_ok=True)

    scenario = _build_cli_scenario(
        model_name=config.model_name,
        scenario_name=config.scenario_name,
        tss_name=config.tss_name,
        dt_hours=config.dt_hours,
        start_year=config.start_year,
        end_year=config.end_year,
        year_gap=config.year_gap,
        retain_existing_output_drop_per_year=config.retain_existing_output_drop_per_year,
        lockout_years=config.lockout_years,
    )
    _ensure_cli_timeseries_inputs(
        energy_system=energy_system,
        demand_name=config.demand_name,
        tss_name=config.tss_name,
        timeseries_dir=config.input_dir,
        project_root=config.project_root,
    )
    backend = CESMOptimizationBackend(
        timeseries_dir=config.input_dir,
        output_dir=config.output_dir,
        results_db_name="db.sqlite",
        demand_name=config.demand_name,
        retain_existing_output_schedule=scenario.retain_existing_output_schedule,
    )
    solution = backend.solve(energy_system, scenario=scenario)
    results_obj = solution.results
    report_path = write_results_report(
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
