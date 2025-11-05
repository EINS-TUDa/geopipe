# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional

from pypeline.energy_system.scenario import Scenario
from pypeline.energy_system.tss import four_times_indices
from pypeline.optimization.validation import assert_fractional, renormalize_to_one
from pypeline.energy_system.technology import Technology


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
    constraints: Dict[str, dict] | None = None
    technologies: Dict[str, Technology] | None = None
    region_technology_metrics: Dict[int, Dict[str, Dict[str, float]]] | None = None


def _shares_from_energy_outputs(tech_to_energy_mwh: Dict[str, float]) -> Dict[str, float]:
    total = float(sum(max(0.0, v) for v in tech_to_energy_mwh.values()))
    if total <= 0:
        return {k: 0.0 for k in tech_to_energy_mwh}
    return {k: float(max(0.0, v)) / total for k, v in tech_to_energy_mwh.items()}


def _try_get_fractional_profile(region_demand) -> Optional[List[float]]:
    """Try several common attribute paths to fetch a fractional profile from a RegionDemand."""
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


def make_flat_schedules(shares: Dict[str, float], hours: int = 8760) -> Dict[str, List[float]]:
    return {t: [float(max(0.0, min(1.0, s)))] * hours for t, s in shares.items()}


def build_om_from_es( energy_system, scenario: Scenario, demand_name: str = "residential_heat", commodity_out: Optional[str] = None, shaped_schedules: Optional[Dict[str, List[float]]] = None) -> OMContext:
    """Build an OMContext from an EnergySystem and Scenario."""
    regions = energy_system.regions
    if not regions:
        raise ValueError("EnergySystem has no regions")

    if commodity_out is None:
        d0 = regions[0].get_demand(demand_name)
        if d0 is None:
            raise ValueError(f"First region has no demand name '{demand_name}'")
        commodity_out = d0.demand.commodity_in if hasattr(d0, "demand") else getattr(d0, "commodity_in", None)
        if commodity_out is None:
            raise ValueError("Could not infer output commodity for selected demand")

    region_ids: List[int] = []
    annual_demand: Dict[int, Dict[int, float]] = {}
    shares_annual: Dict[str, float] = {}

    demand_profile: Optional[List[float]] = None

    region_metrics: Dict[int, Dict[str, Dict[str, float]]] = {}
    tech_objects: Dict[str, Technology] = {}

    for idx, r in enumerate(regions):
        rid = _safe_int_id(getattr(r, "id", getattr(r, "id_", idx)), idx)
        region_ids.append(rid)

        rd = r.get_demand(demand_name)
        if rd is None:
            raise ValueError(f"Region {rid} has no demand '{demand_name}'")

        # AnnualDemand (MWh)
        ann = float(getattr(rd, "value", getattr(rd, "annual", 0.0)))
        for y in scenario.years():
            annual_demand.setdefault(rid, {})[y] = ann

        # demand profile (first available) - renormalize defensively
        if demand_profile is None:
            prof = _try_get_fractional_profile(rd)
            if prof is not None:
                demand_profile = renormalize_to_one(prof)

        # collect annual outputs by tech with matching output commodity
        tech_to_e: Dict[str, float] = {}
        for rt in getattr(r, "region_technologies", []):
            tech = getattr(rt, "technology", None)
            if tech is None:
                continue
            tech_objects.setdefault(getattr(tech, "name", f"tech-{len(tech_objects)}"), tech)
            if getattr(tech, "commodity_out", None) == commodity_out:
                tech_to_e[getattr(tech, "name", "unknown")] = float(
                    getattr(rt, "initial_energy_output", 0.0)
                )
            metrics = region_metrics.setdefault(rid, {})
            metrics[getattr(tech, "name", "unknown")] = {
                "initial_energy_output": float(getattr(rt, "initial_energy_output", 0.0)),
                "initial_capacity": float(getattr(rt, "initial_capacity", 0.0)),
            }

        loc_shares = _shares_from_energy_outputs(tech_to_e)
        for t, s in loc_shares.items():
            shares_annual[t] = shares_annual.get(t, 0.0) + s

    # Normalize system-wide shares so they sum to 1
    total_share = sum(shares_annual.values())
    if total_share > 0:
        shares_annual = {t: s / total_share for t, s in shares_annual.items()}

    # Demand profile default: flat
    hours = 8760
    if demand_profile is None:
        demand_profile = [1.0 / hours] * hours
    assert_fractional(demand_profile, "Demand profile f_h")

    if shaped_schedules:
        schedules: Dict[str, List[float]] = {}
        for t, target_share in shares_annual.items():
            shape = shaped_schedules.get(t, [0.0] * hours)
            shape = [max(0.0, float(x)) for x in shape]
            if sum(shape) == 0:
                schedules[t] = [0.0] * hours
            else:
                norm = renormalize_to_one(shape)
                schedules[t] = [target_share * v for v in norm]
    else:
        schedules = make_flat_schedules(shares_annual, hours=hours)

    tss_idx, tss_w = four_times_indices()
    constraints = getattr(energy_system, "constraints", None)

    return OMContext(
        years=scenario.years(),
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
