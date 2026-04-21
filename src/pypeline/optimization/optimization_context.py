"""Backend-agnostic optimization context.

Defines :class:`OptimizationContext`, a flat numerical representation of an
:class:`~pypeline.energy_system.energy_system.EnergySystem` + :class:`~pypeline.energy_system.scenario.Scenario`
pair.  All domain logic (region topology, demand profiles, technology shares)
is resolved here so that solver backends only need to consume plain dicts /
lists / DataFrames.

Typical use::

    from pypeline.optimization.optimization_context import (
        OptimizationContext,
        build_optimization_context,
    )

    ctx = build_optimization_context(energy_system, scenario)
    # ctx.years, ctx.annual_demand, ctx.technologies, ...
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pypeline.energy_system.core import EnergySystem, RegionDemand, Scenario
from pypeline.energy_system.rule_book import DEFAULT_HEAT_GRID_DEMAND_NAME
from pypeline.optimization.tss import four_times_indices
from pypeline.validation import (
    assert_fractional,
    normalize_shares_or_zero,
    renormalize_to_one,
    to_int_id,
)
from pypeline.energy_technology.technology import (
    Technology,
    split_base_and_district as _split_base_and_district,
)


@dataclass
class OptimizationContext:
    """Flat numerical context derived from an EnergySystem and a Scenario.

    This is the intermediate representation passed to solver backends.
    It contains mostly plain Python types (lists, dicts, floats).  The
    ``technologies`` field carries Technology domain objects because
    backends need access to the full technology specification.

    Attributes:
        years: Scenario years as a list of ints.
        regions: Region IDs as a list of ints.
        commodity: Name of the demand commodity (e.g. ``"residential_heat"``).
        annual_demand: Nested mapping ``{region_id: {year: MWh}}``.
        demand_profile: Fractional hourly profile (length 8760, sums to 1).
        schedules: Per-technology fractional hourly supply schedules
            ``{tech_name: [f_h, ...]}``.
        tss_indices: 0-based hour indices selected by the time-slice scheme.
        tss_weights: Weights corresponding to ``tss_indices``.
        constraints: Optional mapping of constraint name → values dict from
            the EnergySystem (e.g. ``{"min_dhn_throughput_mwh": {...}}``).
        technologies: Optional mapping of technology name → Technology object.
        region_technology_metrics: Optional nested mapping
            ``{region_id: {tech_name: {"initial_capacity": float,
            "initial_energy_output": float}}}``.
    """
    years: List[int]
    regions: List[int]
    commodity: str
    annual_demand: Dict[int, Dict[int, float]]  # region -> year -> MWh
    demand_profile: List[float]                 # l = 8760
    schedules: Dict[str, List[float]]           # {tech_name: fractional share per hour}
    tss_indices: List[int]                      # 0-based hour indices
    tss_weights: List[int]
    constraints: Dict[str, Dict[int, float]] | None = None
    technologies: Dict[str, Technology] | None = None
    region_technology_metrics: Dict[int, Dict[str, Dict[str, float]]] | None = None
    historical_exchanger_targets_mwh: Dict[int, float] | None = None


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


def enforce_historical_fernwaerme_dependency(
    optimization_context: OptimizationContext,
    *,
    end_year: int | None = None,
    year_gap: int | None = None,
) -> Dict[int, float]:
    """Returns exchanger throughput targets by region.

    Districts without historical Fernwaerme keep DHN/heat-exchanger assets at zero initial but buildable.
    """
    region_metrics = optimization_context.region_technology_metrics or {}
    if not isinstance(region_metrics, dict):
        raise TypeError("OptimizationContext.region_technology_metrics must be a dict")

    _ = end_year
    _ = year_gap

    historical_exchanger_targets_mwh: Dict[int, float] = {}

    for rid_raw, tech_map in region_metrics.items():
        rid = to_int_id(rid_raw)
        if not isinstance(tech_map, dict):
            raise TypeError(f"region_technology_metrics[{rid}] must be a dict")

        hx_keys: list[str] = []
        hg_keys: list[str] = []
        for tech_name in list(tech_map.keys()):
            base, _ = _split_base_and_district(str(tech_name))
            base_l = str(base).strip().lower()
            if base_l == "heat_exchanger":
                hx_keys.append(str(tech_name))
            elif base_l == "heat_grid":
                hg_keys.append(str(tech_name))

        def _metrics_for(key: str) -> Dict[str, float]:
            metrics = tech_map.get(key)
            if metrics is None:
                metrics = {}
                tech_map[key] = metrics
            if not isinstance(metrics, dict):
                raise TypeError(f"region_technology_metrics[{rid}][{key}] must be a dict")
            return metrics

        hx_output = 0.0
        hx_capacity = 0.0
        for hx_key in hx_keys:
            hx_metrics = _metrics_for(hx_key)
            hx_output += max(0.0, float(hx_metrics.get("initial_energy_output", 0.0) or 0.0))
            hx_capacity += max(0.0, float(hx_metrics.get("initial_capacity", 0.0) or 0.0))

        has_historical_fernwaerme = bool(hx_output > 0.0)

        if not has_historical_fernwaerme:
            for tech_key in list(dict.fromkeys(hx_keys + hg_keys)):
                metrics = _metrics_for(tech_key)
                metrics["initial_energy_output"] = 0.0
                metrics["initial_capacity"] = 0.0
        else:
            historical_exchanger_targets_mwh[rid] = hx_output
            if hx_capacity <= 0.0:
                hx_capacity = max(hx_output / 8760.0, 0.0)

            if not hg_keys and hx_keys:
                _, district = _split_base_and_district(hx_keys[0])
                inferred_hg_key = "heat_grid" if district is None else f"heat_grid_D{district}"
                tech_map[inferred_hg_key] = {
                    "initial_energy_output": 0.0,
                    "initial_capacity": 0.0,
                }
                hg_keys = [inferred_hg_key]

            for hg_key in hg_keys:
                hg_metrics = _metrics_for(hg_key)
                if float(hg_metrics.get("initial_energy_output", 0.0) or 0.0) <= 0.0:
                    hg_metrics["initial_energy_output"] = float(hx_output)
                if float(hg_metrics.get("initial_capacity", 0.0) or 0.0) <= 0.0 and hx_capacity > 0.0:
                    hg_metrics["initial_capacity"] = float(hx_capacity)

    optimization_context.region_technology_metrics = region_metrics
    optimization_context.historical_exchanger_targets_mwh = historical_exchanger_targets_mwh
    return historical_exchanger_targets_mwh


def build_optimization_context(
    energy_system: EnergySystem,
    scenario: Scenario,
    demand_name: str = DEFAULT_HEAT_GRID_DEMAND_NAME,
    commodity_out: Optional[str] = None,
    shaped_schedules: Optional[Mapping[str, Sequence[float]]] = None,
) -> OptimizationContext:
    """Build an :class:`OptimizationContext` from an EnergySystem and Scenario.

    Args:
        energy_system: The configured energy system.
        scenario: The scenario defining years, discount rate, etc.
        demand_name: Name of the demand entry to use for the commodity and
            annual demand values (default: ``"residential_heat"``).
        commodity_out: Override the demand commodity name.  Inferred from the
            first region's demand entry when *None*.
        shaped_schedules: Optional per-technology hourly shapes (length 8760).
            When provided, each technology's flat share is spread according to
            its shape.  Technologies without a shape entry get a zero schedule.

    Returns:
        A fully populated :class:`OptimizationContext`.
    """
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

    ctx = OptimizationContext(
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

    enforce_historical_fernwaerme_dependency(ctx)
    return ctx

