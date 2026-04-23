"""Backend-agnostic intermediate representation of EnergySystem + Scenario.

``ResolvedSystem`` contains all domain data that an optimization backend needs,
pre-computed for a specific set of scenario years.  It is built once per
``solve()`` call and passed to every backend's input-writer, keeping
backend-specific code free of domain-derivation logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from pypeline.energy_system.imports import Imports
from pypeline.energy_system.pipe import Pipe
from pypeline.optimization.cesm.io_utils import resolve_retain_schedule

if TYPE_CHECKING:
    from pypeline.energy_system.core import EnergySystem, Scenario
    from pypeline.energy_technology.technology import Technology


@dataclass
class ResolvedSystem:
    # Scenario-derived
    scenario_years: list[int]
    discount_rate: float
    lockout_until_year: int

    # Regions
    region_ids: list[int]

    # Primary demand commodity and its normalized 8760-hour profile
    demand_commodity: str
    demand_profile: list[float]

    # Annual demand per region per year: rid -> year -> MWh
    annual_demand: dict[int, dict[int, float]]

    # Per-region technology metrics: rid -> tech_name -> {initial_capacity, initial_energy_output}
    region_metrics: dict[int, dict[str, dict[str, float]]]

    # All technologies referenced by region_technologies
    technologies: dict[str, "Technology"]

    # Commodity imports (electricity buy price + fuel supply prices)
    imports: list[Imports]

    # Retention schedule (None = no retention constraint)
    retain_schedule: list[float] | None

    # Domain constraints (raw from EnergySystem.constraints)
    constraints: dict[str, Any]

    # Local DHN capex per region: rid -> {local_grid_capex_base_eur: float}
    local_dhn_costs: dict[int, dict[str, float]]

    # Inter-district pipe connections
    pipe_connections: list[Pipe] = field(default_factory=list)


def resolve_system(
    energy_system: "EnergySystem",
    scenario: "Scenario",
    *,
    retain_existing_output_schedule: list[float] | None = None,
) -> ResolvedSystem:
    """Build a ``ResolvedSystem`` from an ``EnergySystem`` and a ``Scenario``."""

    scenario_years = scenario.years
    discount_rate = float(scenario.discount_rate)
    lockout_until_year = scenario.lockout_until_year

    retain_schedule = resolve_retain_schedule(
        explicit_schedule=retain_existing_output_schedule,
        scenario=scenario,
    )

    demand_commodity: str | None = None
    demand_profile: list[float] | None = None
    annual_demand: dict[int, dict[int, float]] = {}
    region_metrics: dict[int, dict[str, dict[str, float]]] = {}
    technologies: dict[str, Any] = {}
    region_ids: list[int] = []

    for region in energy_system.regions:
        rid = region.id
        region_ids.append(rid)
        total_ann = 0.0

        for rd in region.region_demands:
            if demand_commodity is None:
                demand_commodity = rd.demand.commodity_in
            if demand_profile is None and rd.profile is not None:
                try:
                    prof = [float(x) for x in rd.profile]
                    if len(prof) == 8760:
                        demand_profile = prof
                except (TypeError, ValueError):
                    pass
            if rd.demand.commodity_in == demand_commodity:
                total_ann += float(rd.value or 0.0)

        annual_demand[rid] = {year: total_ann for year in scenario_years}

        metrics: dict[str, dict[str, float]] = {}
        for rt in region.region_technologies:
            tech = rt.technology
            technologies.setdefault(tech.name, tech)
            metrics[tech.name] = {
                "initial_capacity": float(rt.initial_capacity),
                "initial_energy_output": float(rt.initial_energy_output),
            }
        region_metrics[rid] = metrics

    if demand_profile is None:
        raise ValueError("No 8760-hour demand profile found in any region demand")

    local_dhn_costs = {
        region.id: {"local_grid_capex_base_eur": region.local_dhn_capex_base_eur}
        for region in energy_system.regions
        if region.local_dhn_capex_base_eur is not None
    }
    constraints = dict(energy_system.constraints or {})

    return ResolvedSystem(
        scenario_years=scenario_years,
        discount_rate=discount_rate,
        lockout_until_year=lockout_until_year,
        region_ids=region_ids,
        demand_commodity=demand_commodity or "residential_heat",
        demand_profile=demand_profile,
        annual_demand=annual_demand,
        region_metrics=region_metrics,
        technologies=technologies,
        imports=energy_system.imports,
        retain_schedule=retain_schedule,
        constraints=constraints,
        local_dhn_costs=local_dhn_costs,
        pipe_connections=list(energy_system.pipes or []),
    )
