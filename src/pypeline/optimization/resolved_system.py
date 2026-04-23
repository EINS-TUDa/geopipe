"""Resolved optimization inputs for backend writers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pypeline.energy_system.core import EnergySystem, RegionDemand, Scenario
from pypeline.energy_system.pipe import Pipe
from pypeline.optimization.cesm.io_utils import resolve_retain_schedule
from pypeline.optimization.dhn import enforce_historical_fernwaerme_dependency
from pypeline.validation import (
    assert_fractional,
    renormalize_to_one,
    sanitize_price_map,
    to_int_id,
)
from pypeline.energy_technology.technology import Technology


@dataclass
class ResolvedSystem:
    """Single bundle of resolved optimization inputs for CESM writers."""

    scenario_years: List[int]
    region_ids: List[int]
    demand_commodity: str
    annual_demand: Dict[int, Dict[int, float]]
    demand_profile: List[float]
    technologies: Dict[str, Technology]
    constraints: Dict[str, Dict[int, float]] | None
    region_metrics: Dict[int, Dict[str, Dict[str, float]]]
    historical_exchanger_targets_mwh: Dict[int, float]
    retain_schedule: List[float]
    discount_rate: float
    lockout_until_year: int
    grid_prices: Dict[str, float]
    supply_prices: Dict[str, float]
    pipe_connections: List[Pipe]
    local_dhn_costs: Dict[int, Dict[str, float]]
    data_dir: str | Path | None


def _try_get_fractional_profile(region_demand: RegionDemand) -> Optional[List[float]]:
    prof = region_demand.profile
    if prof is not None:
        try:
            seq = [float(x) for x in prof]
            if len(seq) == 8760:
                return seq
        except (TypeError, ValueError):
            pass
    return None


def _safe_int_id(raw: Any, fallback: int) -> int:
    try:
        return to_int_id(raw)
    except ValueError:
        return int(fallback)


def resolve_system(
    energy_system: EnergySystem,
    scenario: Scenario,
    *,
    demand_name: str = "residential_heat",
    retain_existing_output_schedule: list[float] | None = None,
) -> ResolvedSystem:
    """Resolve EnergySystem + Scenario into a backend-ready ResolvedSystem."""
    regions = energy_system.regions
    if not regions:
        raise ValueError("EnergySystem has no regions")

    scenario_years = list(scenario.years)
    if not scenario_years:
        raise ValueError("Scenario has no years")

    d0 = regions[0].get_demand(demand_name)
    if d0 is None:
        raise ValueError(f"First region has no demand name '{demand_name}'")
    demand_commodity = d0.demand.commodity_in

    region_ids: List[int] = []
    annual_demand: Dict[int, Dict[int, float]] = {}
    demand_profile: Optional[List[float]] = None
    region_metrics_raw: Dict[int, Dict[str, Dict[str, float]]] = {}
    technologies: Dict[str, Technology] = {}

    for idx, region in enumerate(regions):
        rid = _safe_int_id(region.id, idx)
        region_ids.append(rid)

        rd = region.get_demand(demand_name)
        if rd is None:
            raise ValueError(f"Region {rid} has no demand '{demand_name}'")

        ann = rd.annual_value()
        annual_demand[rid] = {year: ann for year in scenario_years}

        if demand_profile is None:
            prof = _try_get_fractional_profile(rd)
            if prof is not None:
                demand_profile = renormalize_to_one(prof)

        metrics = region_metrics_raw.setdefault(rid, {})
        for region_technology in region.region_technologies:
            tech = region_technology.technology
            tech_name = tech.name
            technologies.setdefault(tech_name, tech)
            metrics[tech_name] = {
                "initial_energy_output": float(region_technology.initial_energy_output),
                "initial_capacity": float(region_technology.initial_capacity),
            }

    hours = 8760
    if demand_profile is None:
        demand_profile = [1.0 / hours] * hours
    assert_fractional(demand_profile, "Demand profile f_h")

    region_metrics, historical_targets = enforce_historical_fernwaerme_dependency(region_metrics_raw)

    retain_schedule = resolve_retain_schedule(
        explicit_schedule=retain_existing_output_schedule,
        scenario=scenario,
    )
    if retain_schedule is None:
        raise ValueError("retain_existing_output_schedule must be provided via scenario or argument")

    grid_prices = sanitize_price_map(getattr(energy_system, "grid_prices", None))
    supply_prices = sanitize_price_map(getattr(energy_system, "supply_prices", None))
    if not grid_prices:
        raise ValueError("Missing grid price map on EnergySystem.grid_prices")
    if not supply_prices:
        raise ValueError("Missing supply price map on EnergySystem.supply_prices")

    if getattr(energy_system, "inter_district_pipe_specs", None):
        raise ValueError(
            "inter_district_pipe_specs is no longer supported. "
            "Provide explicit EnergySystem.pipes instead."
        )

    local_dhn_costs = {
        region.id: {"local_grid_capex_base_eur": region.local_dhn_capex_base_eur}
        for region in energy_system.regions
        if region.local_dhn_capex_base_eur is not None
    }

    return ResolvedSystem(
        scenario_years=scenario_years,
        region_ids=region_ids,
        demand_commodity=demand_commodity,
        annual_demand=annual_demand,
        demand_profile=demand_profile,
        technologies=technologies,
        constraints=energy_system.constraints,
        region_metrics=region_metrics,
        historical_exchanger_targets_mwh=historical_targets,
        retain_schedule=[float(v) for v in retain_schedule],
        discount_rate=float(getattr(scenario, "discount_rate")),
        lockout_until_year=int(scenario_years[0] + max(0, int(getattr(scenario, "lockout_years")))),
        grid_prices=grid_prices,
        supply_prices=supply_prices,
        pipe_connections=list(getattr(energy_system, "pipes", []) or []),
        local_dhn_costs=local_dhn_costs,
        data_dir=getattr(energy_system, "data_dir", None),
    )
