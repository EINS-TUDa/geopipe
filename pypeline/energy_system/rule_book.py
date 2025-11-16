from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from pypeline.data.data_registry import DataRegistry
from pypeline.data.datasets import CensusTechnology
from pypeline.energy_technology.technology import Technology


HEAT_EXCHANGER_NAMES: tuple[str, ...] = ("heat_exchanger", "ind_district_heating_connection")


def _matches_tech_name(name: str | None, candidates: tuple[str, ...]) -> bool:
    """Return True if `name` matches any candidate or its district clones."""
    if not name:
        return False
    if name in candidates:
        return True
    if "_D" in name:
        base = name.rsplit("_D", 1)[0]
        if base in candidates:
            return True
    return False


def _is_central_heat_supply(name: str | None) -> bool:
    if not name:
        return False
    base = name
    if "_U" in base:
        base, _ = base.rsplit("_U", 1)
    if "_D" in base:
        base, _ = base.rsplit("_D", 1)
    return base.startswith("cen_")

if TYPE_CHECKING:
    from pypeline.energy_system.region import Region


def _extract_region_id(region: "Region") -> Optional[int]:
    try:
        return int(region.id)
    except Exception:
        return None


class RegionRuleBook:
    def __init__(self):
        self.rules = []

    def add_rule(self, rule: 'RegionRule'):
        if not isinstance(rule, RegionRule):
            raise TypeError(f"Only RegionRules can be added to the RegionRuleBook, not {type(rule).__name__}")
        self.rules.append(rule)

    def apply(self, region):
        for rule in self.rules:
            region = rule.apply(region)
        return region

class EnergySystemRuleBook:
    def __init__(self):
        self.rules = []

    def add_rule(self, rule: 'EnergySystemRule'):
        if not isinstance(rule, EnergySystemRule):
            raise TypeError(f"Only EnergySystemRules can be added to the EnergySystemRuleBook, not {type(rule).__name__}")
        self.rules.append(rule)

    def apply(self, energy_system):
        for rule in self.rules:
            energy_system = rule.apply(energy_system)
        return energy_system

class Rule(ABC):
    """
    Possible rules can be:
    If high building density: HP is forbidden
    Certain technology only exists in certain regions
    """
    pass

class RegionRule(Rule):
    """Base class for rules that apply to a specific region."""
    @abstractmethod
    def apply(self, region):
        pass

class EnergySystemRule(Rule):
    """Base class for rules that apply to the entire energy system."""
    @abstractmethod
    def apply(self, energy_system):
        pass

class LimitTechnologyInRegionRule(RegionRule):
    def __init__(self, technology, condition):
        self.technology = technology
        self.condition = condition

    def apply(self, region):
        """Applies the rule to a given region."""
        if self.condition(region):
            region.restrict_technology(self.technology)

class MinimumDHNThroughputRule(EnergySystemRule):
    """
    Require a minimum annual heat delivered by DHN-like techs for the given demand.
    Writes per-region targets (MWh) to energy_system.constraints['min_dhn_throughput_mwh'].
    """
    def __init__(self,
                 demand_name="residential_heat",
                 min_share=None,                     
                 min_mwh_by_region=None,             
                 dhn_tech_names=None):               
        self.demand_name = demand_name
        self.min_share = min_share
        self.min_mwh_by_region = min_mwh_by_region
        self.dhn_tech_names = (
            tuple(dhn_tech_names)
            if dhn_tech_names
            else HEAT_EXCHANGER_NAMES + ("heat_grid",)
        )

    def apply(self, energy_system):
        constraints = energy_system.constraints if energy_system.constraints is not None else {}
        targets: Dict[int, float] = {}

        for region in energy_system.regions or []:
            rid = _extract_region_id(region)
            if rid is None:
                continue
            rd = region.get_demand(self.demand_name)
            if rd is None:
                continue

            annual_mwh = rd.annual_value()

            if self.min_mwh_by_region and rid in self.min_mwh_by_region:
                target = float(self.min_mwh_by_region[rid])
            elif self.min_share is not None:
                target = max(0.0, float(self.min_share)) * annual_mwh
            else:
                continue

            has_dhn = False
            for rt in region.region_technologies or []:
                tech = rt.technology
                if tech is None:
                    continue
                tech_name = tech.name
                if _matches_tech_name(tech_name, self.dhn_tech_names) or _is_central_heat_supply(tech_name):
                    has_dhn = True
                    break
            if not has_dhn:
                continue

            targets[rid] = target

        constraints.setdefault("min_dhn_throughput_mwh", {}).update(targets)
        energy_system.constraints = constraints
        return energy_system


class MinimumHeatGridOutputRule(RegionRule):
    """Ensure heat-grid-supplied technologies meet a minimum output by rescaling shares."""

    def __init__(self,
                 demand_name: str = "residential_heat",
                 min_output_mwh: float = 1.0,
                 min_share: float | None = None,
                 heat_grid_names: tuple[str, ...] | None = None):
        self.demand_name = demand_name
        self.min_output_mwh = float(min_output_mwh)
        self.min_share = None if min_share is None else float(min_share)
        self.heat_grid_names = heat_grid_names or HEAT_EXCHANGER_NAMES

    def apply(self, region):
        technologies = region.region_technologies or []
        if not technologies:
            return region

        region_demand = region.get_demand(self.demand_name)
        if region_demand is None:
            return region

        commodity = region_demand.demand.commodity_in
        if not commodity:
            return region

        techs = [
            rt for rt in technologies
            if rt.technology is not None and rt.technology.commodity_out == commodity
        ]
        if not techs:
            return region

        heat_grid_techs = [
            rt
            for rt in techs
            if rt.technology is not None and _matches_tech_name(rt.technology.name, self.heat_grid_names)
        ]
        if not heat_grid_techs:
            return region

        total_output = sum(float(rt.initial_energy_output or 0.0) for rt in techs)
        if total_output <= 0.0:
            return region

        current_heat_grid_output = sum(float(rt.initial_energy_output or 0.0) for rt in heat_grid_techs)

        min_share = max(0.0, self.min_share) if self.min_share is not None else None
        min_output = max(self.min_output_mwh, (min_share or 0.0) * total_output)
        min_output = min(min_output, total_output)

        if current_heat_grid_output >= min_output or min_output <= 0.0:
            return region

        remaining_techs = [rt for rt in techs if rt not in heat_grid_techs]
        if not remaining_techs:
            new_target = min_output
            if new_target <= 0:
                return region
            if current_heat_grid_output > 0:
                scale = new_target / current_heat_grid_output
                for hg in heat_grid_techs:
                    hg.initial_energy_output *= scale
                    if hg.initial_capacity is not None:
                        hg.initial_capacity *= scale
            else:
                heat_grid_techs[0].initial_energy_output = new_target
                if heat_grid_techs[0].initial_capacity is not None:
                    heat_grid_techs[0].initial_capacity = new_target
            return region

        current_remaining_output = sum(float(rt.initial_energy_output or 0.0) for rt in remaining_techs)
        if current_remaining_output <= 0.0:
            target = min_output
            if current_heat_grid_output > 0:
                scale = target / current_heat_grid_output
                for hg in heat_grid_techs:
                    hg.initial_energy_output *= scale
                    if hg.initial_capacity is not None:
                        hg.initial_capacity *= scale
            else:
                heat_grid_techs[0].initial_energy_output = target
                if heat_grid_techs[0].initial_capacity is not None:
                    heat_grid_techs[0].initial_capacity = target
            return region

        target_heat_output = min_output
        other_target_total = max(total_output - target_heat_output, 0.0)

        if current_heat_grid_output > 0.0:
            heat_scale = target_heat_output / current_heat_grid_output
            for hg in heat_grid_techs:
                hg.initial_energy_output *= heat_scale
                if hg.initial_capacity is not None:
                    hg.initial_capacity *= heat_scale
        else:
            # Distribute target entirely to the first heat-grid tech
            heat_grid_techs[0].initial_energy_output = target_heat_output
            if heat_grid_techs[0].initial_capacity is not None:
                heat_grid_techs[0].initial_capacity = target_heat_output
            for hg in heat_grid_techs[1:]:
                hg.initial_energy_output = 0.0
                if hg.initial_capacity is not None:
                    hg.initial_capacity = 0.0

        if current_remaining_output > 0.0:
            other_scale = other_target_total / current_remaining_output
            for other in remaining_techs:
                other.initial_energy_output *= other_scale
                if other.initial_capacity is not None:
                    other.initial_capacity *= other_scale

        return region


class MinimumHeatGridConstraintRule(EnergySystemRule):
    """Add energy-system level constraints enforcing minimum heat-grid output per region."""

    def __init__(
        self,
        demand_name: str = "residential_heat",
        min_output_mwh: float = 1.0,
        min_share: float | None = None,
        min_mwh_by_region: Optional[Dict[int, float]] = None,
        heat_grid_names: tuple[str, ...] | None = None,
    ) -> None:
        self.demand_name = demand_name
        self.min_output_mwh = float(min_output_mwh)
        self.min_share = None if min_share is None else float(min_share)
        self.min_mwh_by_region = min_mwh_by_region or {}
        self.heat_grid_names = heat_grid_names or HEAT_EXCHANGER_NAMES

    def apply(self, energy_system):
        regions = energy_system.regions or []
        if not regions:
            return energy_system

        targets: Dict[int, float] = {}

        for region in regions:
            rid = _extract_region_id(region)
            if rid is None:
                continue

            rd = region.get_demand(self.demand_name)
            if rd is None:
                continue

            annual_mwh = rd.annual_value()
            if annual_mwh <= 0.0:
                continue

            if rid in self.min_mwh_by_region:
                target = float(self.min_mwh_by_region[rid])
            else:
                target = max(self.min_output_mwh, 0.0)
                if self.min_share is not None:
                    target = max(target, max(0.0, self.min_share) * annual_mwh)

            if target <= 0.0:
                continue

            target = min(target, annual_mwh)

            has_heat_grid = any(
                rt.technology is not None and _matches_tech_name(rt.technology.name, self.heat_grid_names)
                for rt in region.region_technologies or []
            )
            if not has_heat_grid:
                continue

            targets[rid] = target

        if not targets:
            return energy_system

        constraints: Dict[str, Dict[int, float]] = (
            energy_system.constraints if energy_system.constraints is not None else {}
        )
        key = f"min_heat_grid_{self.demand_name}"
        existing = constraints.get(key, {}) if isinstance(constraints.get(key, {}), dict) else {}
        existing.update(targets)
        constraints[key] = existing
        energy_system.constraints = constraints
        return energy_system


class HeatExchangerCostAdjustmentRule(RegionRule):
    """Adjust heat-exchanger CAPEX based on decentralized heating shares from Census 2022."""

    def __init__(
        self,
    data_registry: DataRegistry | None,
    dataset_type: str = "residential_heat_technology_shares",
    heat_exchanger_names: tuple[str, ...] = HEAT_EXCHANGER_NAMES,
        decentralized_keys: tuple[str, ...] | None = None,
        cost_attribute: str = "capex_cost_power",
        scale_factor: float = 1.0,
        min_factor: float = 1.0,
    ) -> None:
        self.data_registry = data_registry
        self.dataset_type = dataset_type
        self.heat_exchanger_names = tuple(heat_exchanger_names)
        if decentralized_keys:
            self.decentralized_keys = tuple(str(key) for key in decentralized_keys)
        else:
            self.decentralized_keys = tuple(
                ct.value for ct in CensusTechnology if ct is not CensusTechnology.District_Heating
            )
        self.cost_attribute = cost_attribute
        self.scale_factor = float(scale_factor)
        self.min_factor = float(min_factor)

    def _normalized_shares(self, shares: Dict[Any, Any]) -> Dict[str, float]:
        normalized: Dict[str, float] = {}
        for key, value in shares.items():
            str_key = key.value if hasattr(key, "value") else str(key)
            try:
                normalized[str_key] = float(value)
            except Exception:
                continue
        return normalized

    def _clone_with_capex(self, tech: Technology, new_capex: float) -> Technology:
        return Technology(
            name=tech.name,
            commodity_in=tech.commodity_in,
            commodity_out=tech.commodity_out,
            efficiency=tech.efficiency,
            technical_lifetime=tech.technical_lifetime,
            opex_cost_energy=tech.opex_cost_energy,
            opex_cost_power=tech.opex_cost_power,
            capex_cost_power=new_capex,
            capex_cost_base=tech.capex_cost_base,
            cap_min=tech.cap_min,
            cap_max=tech.cap_max,
            max_units=tech.max_units,
            availability_profile=tech.availability_profile,
            stage=tech.stage,
            category=tech.category,
        )

    def apply(self, region):
        if not self.data_registry:
            return region

        polygon = region.polygon
        if polygon is None:
            return region

        try:
            shares = self.data_registry.query({"type": self.dataset_type, "region": polygon})
        except Exception:
            return region

        if not shares:
            return region

        normalized = self._normalized_shares(shares)
        if not normalized:
            return region

        decentralized_share = sum(normalized.get(key, 0.0) for key in self.decentralized_keys)
        decentralized_share = max(0.0, min(1.0, float(decentralized_share)))

        factor = max(self.min_factor, 1.0 + self.scale_factor * decentralized_share)
        if factor <= 0.0 or abs(factor - 1.0) < 1e-9:
            return region

        for rt in region.region_technologies or []:
            tech = rt.technology
            if tech is None or not _matches_tech_name(tech.name, self.heat_exchanger_names):
                continue

            base_value = getattr(tech, self.cost_attribute, None)
            if base_value is None:
                continue

            try:
                new_value = float(base_value) * factor
            except Exception:
                continue

            if abs(new_value - base_value) < 1e-9:
                continue

            rt.technology = self._clone_with_capex(tech, new_value)

        return region
