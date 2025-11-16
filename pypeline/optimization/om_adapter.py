from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence

from pypeline.energy_system.demand import RegionDemand
from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_system.scenario import Scenario
from pypeline.energy_system.tss import four_times_indices
from pypeline.optimization.validation import assert_fractional, renormalize_to_one
from pypeline.energy_technology.technology import Technology


@dataclass
class OMContext:
    """Backend-agnostic optimization context derived from the EnergySystem and a Scenario."""
    years: List[int]
    regions: List[int]
    commodity: str
    annual_demand: Dict[int, Dict[int, float]]  # region -> year -> MWh
    demand_profile: List[float]                 # l = 8760
    schedules: Dict[str, List[float]]           # Schedules g_{tech,h}: tech -> fractional share of demand per hour
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
    """Try common attribute paths on a RegionDemand to extract an 8760-shape profile."""
    prof = getattr(region_demand, "profile", None)
    if prof is not None:
        try:
            seq = [float(x) for x in prof]
            if len(seq) == 8760:
                return seq
        except Exception:
            pass
    
    d = getattr(region_demand, "demand", None)
    if d is not None:
        prof2 = getattr(d, "profile", None)
        if prof2 is not None:
            try:
                seq = [float(x) for x in prof2]
                if len(seq) == 8760:
                    return seq
            except Exception:
                pass
    return None


def _safe_int_id(raw, fallback: int) -> int:
    """Handle pandas Series / NumPy scalars / strings gracefully when converting to int."""
    if hasattr(raw, "iloc"):
        try:
            raw = raw.iloc[0]
        except Exception:
            pass
    try:
        import numpy as _np  
        if isinstance(raw, _np.generic):
            raw = raw.item()
    except Exception:
        pass
    try:
        return int(raw)
    except Exception:
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
    demand_name: str = "residential_heat",
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
        commodity_out = d0.demand.commodity_in if hasattr(d0, "demand") else getattr(d0, "commodity_in", None)
        if commodity_out is None:
            raise ValueError("Could not infer output commodity for selected demand")

    region_ids: List[int] = []
    annual_demand: Dict[int, Dict[int, float]] = {}
    shares_accumulated: defaultdict[str, float] = defaultdict(float)

    demand_profile: Optional[List[float]] = None

    region_metrics: Dict[int, Dict[str, Dict[str, float]]] = {}
    tech_objects: Dict[str, Technology] = {}

    for idx, region in enumerate(regions):
        rid = _safe_int_id(getattr(region, "id", getattr(region, "id_", idx)), idx)
        region_ids.append(rid)

        rd = region.get_demand(demand_name)
        if rd is None:
            raise ValueError(f"Region {rid} has no demand '{demand_name}'")

        ann = float(getattr(rd, "value", getattr(rd, "annual", 0.0)))
        annual_demand[rid] = {year: ann for year in scenario_years}

        if demand_profile is None:
            prof = _try_get_fractional_profile(rd)
            if prof is not None:
                demand_profile = renormalize_to_one(prof)

        tech_to_energy: Dict[str, float] = {}
        metrics = region_metrics.setdefault(rid, {})
        for region_technology in getattr(region, "region_technologies", []):
            tech = getattr(region_technology, "technology", None)
            if tech is None:
                continue
            tech_name = getattr(tech, "name", f"tech-{len(tech_objects)}")
            tech_objects.setdefault(tech_name, tech)
            metrics[tech_name] = {
                "initial_energy_output": float(getattr(region_technology, "initial_energy_output", 0.0)),
                "initial_capacity": float(getattr(region_technology, "initial_capacity", 0.0)),
            }
            if getattr(tech, "commodity_out", None) == commodity_out:
                tech_to_energy[tech_name] = float(getattr(region_technology, "initial_energy_output", 0.0))

        for tech_name, value in _shares_from_energy_outputs(tech_to_energy).items():
            shares_accumulated[tech_name] += value

    total_share = sum(shares_accumulated.values())
    if total_share > 0:
        shares_annual = {t: s / total_share for t, s in shares_accumulated.items()}
    else:
        shares_annual = {t: 0.0 for t in shares_accumulated}

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
    constraints = getattr(energy_system, "constraints", None)

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
