from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence
import numpy as np

from pypeline.energy_system.demand import RegionDemand
from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_system.rule_book import DEFAULT_HEAT_GRID_DEMAND_NAME
from pypeline.energy_system.io_utils import (
    commodity_config_from_energy_system,
    polygons_from_energy_system,
    resolve_retain_schedule,
)
from pypeline.energy_system.scenario import Scenario
from pypeline.energy_system.tss import four_times_indices
from pypeline.validation import (
    assert_fractional,
    normalize_shares_or_zero,
    renormalize_to_one,
    to_int_id,
)
from pypeline.energy_technology.technology import Technology


@dataclass
class OMContext:
    """Backend-agnostic optimization context derived from the EnergySystem and a Scenario."""
    years: List[int]
    regions: List[int]
    commodity: str
    annual_demand: Dict[int, Dict[int, float]]  # region -> year -> MWh
    demand_profile: List[float]                 # l = 8760
    schedules: Dict[str, List[float]]           # {tech,h}: fractional share of demand per hour
    tss_indices: List[int]                      # 0-based hour indices
    tss_weights: List[int]
    constraints: Dict[str, Dict[int, float]] | None = None
    technologies: Dict[str, Technology] | None = None
    region_technology_metrics: Dict[int, Dict[str, Dict[str, float]]] | None = None


def _shares_from_energy_outputs(tech_to_energy_mwh: Mapping[str, float]) -> Dict[str, float]:
    """Convert annual technology outputs into non-negative shares that sum to one."""
    total = float(sum(max(0.0, v) for v in tech_to_energy_mwh.values()))
    if total <= 0:
        return {k: 0.0 for k in tech_to_energy_mwh}
    return {k: float(max(0.0, v)) / total for k, v in tech_to_energy_mwh.items()}


def _try_get_fractional_profile(region_demand: RegionDemand) -> Optional[List[float]]:
    """Extract an 8760-shape profile from RegionDemand.profile when available."""
    prof = region_demand.profile
    if prof is not None:
        try:
            seq = [float(x) for x in prof]
            if len(seq) == 8760:
                return seq
        except (TypeError, ValueError):
            pass
    return None


def _safe_int_id(raw, fallback: int) -> int:
    """Handle pandas Series / NumPy scalars / strings gracefully when converting to int."""
    try:
        return to_int_id(raw)
    except ValueError:
        return int(fallback)


def make_flat_schedules(shares: Mapping[str, float], hours: int = 8760) -> Dict[str, List[float]]:
    """Spread each technology share evenly across the modeled year."""
    return {t: [float(max(0.0, min(1.0, s)))] * hours for t, s in shares.items()}


def _shape_schedule_for_share(
    shape: Sequence[float],
    target_share: float,
    hours: int,
) -> List[float]:
    """Renormalize an arbitrary shape to match a target annual share."""
    sanitized = [max(0.0, float(x)) for x in shape]
    if len(sanitized) != hours:
        if len(sanitized) > hours:
            sanitized = sanitized[:hours]
        else:
            sanitized.extend([0.0] * (hours - len(sanitized)))
    if not sanitized or sum(sanitized) == 0:
        return [0.0] * hours
    normalized = renormalize_to_one(sanitized)
    return [target_share * v for v in normalized]


def build_om_from_es(
    energy_system: EnergySystem,
    scenario: Scenario,
    demand_name: str = DEFAULT_HEAT_GRID_DEMAND_NAME,
    commodity_out: Optional[str] = None,
    shaped_schedules: Optional[Mapping[str, Sequence[float]]] = None,
) -> OMContext:
    """Build an OMContext from an EnergySystem and Scenario."""
    regions = energy_system.regions
    if not regions:
        raise ValueError("EnergySystem has no regions")

    scenario_years = scenario.years()

    if commodity_out is None:
        d0 = regions[0].get_demand(demand_name)
        if d0 is None:
            raise ValueError(f"First region has no demand name '{demand_name}'")
        commodity_out = d0.demand.commodity_in

    region_ids: List[int] = []
    annual_demand: Dict[int, Dict[int, float]] = {}
    shares_accumulated: defaultdict[str, float] = defaultdict(float)
    demand_profile: Optional[List[float]] = None
    region_metrics: Dict[int, Dict[str, Dict[str, float]]] = {}
    tech_objects: Dict[str, Technology] = {}

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

        tech_to_energy: Dict[str, float] = {}
        metrics = region_metrics.setdefault(rid, {})
        for region_technology in region.region_technologies:
            tech = region_technology.technology
            tech_name = tech.name
            tech_objects.setdefault(tech_name, tech)
            metrics[tech_name] = {
                "initial_energy_output": float(region_technology.initial_energy_output),
                "initial_capacity": float(region_technology.initial_capacity),
            }
            if tech.commodity_out == commodity_out:
                tech_to_energy[tech_name] = float(region_technology.initial_energy_output)

        for tech_name, value in _shares_from_energy_outputs(tech_to_energy).items():
            shares_accumulated[tech_name] += value

    shares_annual = normalize_shares_or_zero(shares_accumulated)

    hours = 8760
    if demand_profile is None:
        demand_profile = [1.0 / hours] * hours
    assert_fractional(demand_profile, "Demand profile f_h")

    if shaped_schedules:
        schedules: Dict[str, List[float]] = {}
        for tech_name, target_share in shares_annual.items():
            shape = shaped_schedules.get(tech_name)
            if shape is None:
                schedules[tech_name] = [0.0] * hours
                continue
            schedules[tech_name] = _shape_schedule_for_share(shape, target_share, hours)
    else:
        schedules = make_flat_schedules(shares_annual, hours=hours)

    tss_idx, tss_w = four_times_indices()
    constraints = energy_system.constraints

    return OMContext(
        years=scenario_years,
        regions=region_ids,
        commodity=commodity_out,
        annual_demand=annual_demand,
        demand_profile=demand_profile,
        schedules=schedules,
        tss_indices=tss_idx,
        tss_weights=tss_w,
        constraints=constraints,
        technologies=tech_objects,
        region_technology_metrics=region_metrics,
    )
