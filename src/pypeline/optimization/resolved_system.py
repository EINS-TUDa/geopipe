"""Backend-agnostic intermediate representation of EnergySystem + Scenario.

``ResolvedSystem`` contains all domain data that an optimization backend needs,
pre-computed for a specific set of scenario years.  It is built once per
``solve()`` call and passed to every backend's input-writer, keeping
backend-specific code free of domain-derivation logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from pypeline.energy_system.core import EnergySystem, Scenario
    from pypeline.energy_technology.technology import Technology


@dataclass
class ResolvedSystem:
    # Scenario-derived
    scenario_years: list[int]
    discount_rate: float
    lockout_years: int

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

    # Commodity pricing
    grid_prices: dict[str, float]
    supply_prices: dict[str, float]

    # Retention schedule (None = no retention constraint)
    retain_schedule: list[float] | None

    # Domain constraints (raw from EnergySystem.constraints)
    constraints: dict[str, Any]

    # Local DHN capex per region: rid -> {local_grid_capex_base_eur: float}
    local_dhn_costs: dict[int, dict[str, float]]

    # Inter-district pipe connections (non-free pairs)
    pipe_pairs: list[tuple[int, int]]
    pipe_specs: dict[tuple[int, int], dict[str, Any]]

    # Groups of districts connected by free pipes (each group shares a pooled commodity)
    free_pipe_groups: list[frozenset[int]] = field(default_factory=list)


def resolve_system(
    energy_system: "EnergySystem",
    scenario: "Scenario",
    *,
    retain_existing_output_schedule: list[float] | None = None,
) -> ResolvedSystem:
    """Build a ``ResolvedSystem`` from an ``EnergySystem`` and a ``Scenario``."""
    from pypeline.optimization.cesm.io_utils import resolve_retain_schedule

    scenario_years = scenario.years()
    discount_rate = float(scenario.discount_rate)
    lockout_years = max(0, int(scenario.lockout_years))

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

    # Pipe connections
    pipe_pairs: list[tuple[int, int]] = []
    pipe_specs: dict[tuple[int, int], dict[str, Any]] = {}
    free_pipe_groups: list[frozenset[int]] = []

    raw_specs = energy_system.inter_district_pipe_specs or {}
    if raw_specs:
        all_specs = {(int(i), int(j)): dict(specs or {}) for (i, j), specs in raw_specs.items()}
        free_set = {pair for pair, specs in all_specs.items() if bool(specs.get("is_free", False))}
        pipe_specs = {pair: specs for pair, specs in all_specs.items() if pair not in free_set}
        pipe_pairs = sorted(pipe_specs)

        if free_set:
            adjacency: dict[int, set[int]] = {}
            for (i, j) in free_set:
                adjacency.setdefault(i, set()).add(j)
                adjacency.setdefault(j, set()).add(i)
            visited: set[int] = set()
            for start in list(adjacency):
                if start in visited:
                    continue
                stack = [start]
                component: list[int] = []
                while stack:
                    node = stack.pop()
                    if node in visited:
                        continue
                    visited.add(node)
                    component.append(node)
                    stack.extend(adjacency.get(node, set()) - visited)
                if len(component) > 1:
                    free_pipe_groups.append(frozenset(component))

    return ResolvedSystem(
        scenario_years=scenario_years,
        discount_rate=discount_rate,
        lockout_years=lockout_years,
        region_ids=region_ids,
        demand_commodity=demand_commodity or "residential_heat",
        demand_profile=demand_profile,
        annual_demand=annual_demand,
        region_metrics=region_metrics,
        technologies=technologies,
        grid_prices=dict(energy_system.grid_prices or {}),
        supply_prices=dict(energy_system.supply_prices or {}),
        retain_schedule=retain_schedule,
        constraints=constraints,
        local_dhn_costs=local_dhn_costs,
        pipe_pairs=pipe_pairs,
        pipe_specs=pipe_specs,
        free_pipe_groups=free_pipe_groups,
    )
