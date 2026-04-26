from __future__ import annotations
from pathlib import Path
from typing import Any
from pypeline.data.default_registry import get_default_data_registry
from pypeline.energy_system.builder import EnergySystemBuilder
from pypeline.energy_system.core import Scenario
from pypeline.injection import apply_injected_techs
from pypeline.energy_system.rule_book import (CommodityActivationYearRule,EnergySystemRuleBook,TechnologyActivationYearRule)
from pypeline.optimization.cesm import CESMOptimizationBackend




def create_cesm_backend(
    *,
    demand_name: str,
    timeseries_dir: Path,
    output_dir: Path,
) -> CESMOptimizationBackend:
    return CESMOptimizationBackend(
        timeseries_dir=timeseries_dir,
        output_dir=output_dir,
        results_db_name="db.sqlite",
        demand_name=demand_name,
    )

def build_energy_system(
    *,
    model_name: str,
    street_network,
    heating_shares_file: Path,
    region_builder_config_overrides: dict | None,
    rulebook: EnergySystemRuleBook | None = None,
    injected_techs: list[dict[str, Any]] | None = None,
    commodity_activation_year_by_name: dict[str, int] | None = None,
    technology_activation_year_by_name: dict[str, int] | None = None,
):


    builder = EnergySystemBuilder(energy_system_name=model_name)
    builder.set_street_network(street_network)
    builder.set_demands(default=True)
    builder.set_data_registry(
        create_local_data_registry(
            heating_shares_file=heating_shares_file,
        )
    )
    builder.set_default_region_builder_config()
    if region_builder_config_overrides:
        builder.set_region_builder_config(region_builder_config_overrides, merge=True)

    rb = EnergySystemRuleBook()
    if rulebook is not None:
        for rule in getattr(rulebook, "rules", []):
            rb.add_rule(rule)
    if commodity_activation_year_by_name:
        rb.add_rule(
            CommodityActivationYearRule(
                activation_year_by_commodity=commodity_activation_year_by_name,
                overwrite=True,
            )
        )
    if technology_activation_year_by_name:
        rb.add_rule(
            TechnologyActivationYearRule(
                activation_year_by_technology=technology_activation_year_by_name,
                overwrite=True,
            )
        )

    builder.set_energy_system_rule_book(rb)

    energy_system = builder.build()
    apply_injected_techs(energy_system, injected_techs or [])

    return energy_system

def build_scenario(
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
    run_name = f"{model_name}-{scenario_name}"
    return Scenario(
        name=run_name,
        start_year=start_year,
        end_year=end_year,
        year_gap=year_gap,
        dt_hours=dt_hours,
        tss=tss_name,
        retain_existing_output_drop_per_year=retain_existing_output_drop_per_year,
        lockout_years=lockout_years,
    )