from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Any
from pypeline.data.default_registry import get_default_data_registry
from pypeline.energy_system.builder import EnergySystemBuilder
from pypeline.energy_system.core import Scenario
from pypeline.injection import apply_injected_techs
from pypeline.energy_system.rule_book import EnergySystemRuleBook
from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.optimization.cesm import CESMOptimizationBackend


def create_local_data_registry(heat_demand_file: Path, heating_shares_file: Path):
    if not heat_demand_file.exists():
        raise FileNotFoundError(f"Missing heat demand file: {heat_demand_file}")
    if not heating_shares_file.exists():
        raise FileNotFoundError(f"Missing heating shares file: {heating_shares_file}")

    return get_default_data_registry(
        mode="local",
        local_heat_demand_file=heat_demand_file,
        local_heating_shares_file=heating_shares_file,
    )

def create_cesm_backend(
    project_root: Path,
    *,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    dt_hours: int,
    scenario: Scenario,
    demand_name: str,
) -> CESMOptimizationBackend:
    workdir = (project_root / "CESM").resolve()
    existing_py_path = os.environ.get("PYTHONPATH")
    if existing_py_path:
        os.environ["PYTHONPATH"] = f"{project_root}{os.pathsep}{existing_py_path}"
    else:
        os.environ["PYTHONPATH"] = str(project_root)

    return CESMOptimizationBackend(
        workdir=str(workdir),
        cli=[sys.executable, "-m", "pypeline.optimization.cesm.cli"],
        run_args=["--workdir", str(workdir), "-m", model_name, "-s", scenario_name],
        run_subdir=f"{model_name}-{scenario_name}",
        results_db_name="db.sqlite",
        write_inputs=True,
        model_name=model_name,
        scenario_name=scenario_name,
        tss_name=tss_name,
        dt_hours=dt_hours,
        scenario=scenario,
        demand_name=demand_name,
    )

def build_energy_system(
    *,
    model_name: str,
    region_topologies: list,
    street_network,
    heat_demand_file: Path,
    heating_shares_file: Path,
    region_builder_config_overrides: dict | None,
    rulebook: EnergySystemRuleBook | None = None,
    injected_techs: list[dict[str, Any]] | None = None,
):
    tech_registry = TechnologyRegistry()
    tech_registry.load_from_default()

    builder = EnergySystemBuilder(energy_system_name=model_name)
    builder.set_region_topologies(region_topologies)
    builder.set_street_network(street_network)
    builder.set_demands(default=True)
    builder.set_technology_registry(tech_registry)
    builder.set_data_registry(create_local_data_registry(heat_demand_file, heating_shares_file))
    builder.set_default_region_builder_config()
    if region_builder_config_overrides:
        builder.set_region_builder_config(region_builder_config_overrides, merge=True)
    builder.set_energy_system_rule_book(rulebook if rulebook is not None else EnergySystemRuleBook())

    energy_system = builder.build()
    apply_injected_techs(energy_system, injected_techs or [])
    return energy_system

def build_scenario(
    *,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    start_year: int,
    end_year: int,
    year_gap: int,
    retain_existing_output_drop_per_year: float,
    lockout_years: int,
) -> Scenario:
    run_name = f"{model_name}-{scenario_name}"
    return Scenario(
        name=run_name,
        start_year=start_year,
        end_year=end_year,
        year_gap=year_gap,
        tss=tss_name,
        retain_existing_output_drop_per_year=retain_existing_output_drop_per_year,
        lockout_years=lockout_years,
    )