from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pypeline.energy_system.region import Region


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
                 min_share=None,                     # bspw 0.20 via DHN
                 min_mwh_by_region=None,             # dict {region_id: MWh}
                 dhn_tech_names=None):               
        self.demand_name = demand_name
        self.min_share = min_share
        self.min_mwh_by_region = min_mwh_by_region
        self.dhn_tech_names = tuple(dhn_tech_names) if dhn_tech_names else (
            # include connection so delivered energy is counted on the consumer side
            "ind_district_heating_connection",
            "heat_grid", "cen_heat_pump", "cen_gas_boiler", "cen_chp"
        )

    def apply(self, energy_system):
        constraints = getattr(energy_system, "constraints", {})
        targets = {}

        for region in energy_system.regions:
            raw_id = getattr(region, "id", getattr(region, "id_", 0))
            if hasattr(raw_id, "iloc"):           # pandas Series length 1
                raw_id = raw_id.iloc[0]
            try:
                import numpy as _np
                if isinstance(raw_id, _np.generic):  # numpy -> python
                    raw_id = raw_id.item()
            except Exception:
                pass
            rid = int(raw_id)
            rd = region.get_demand(self.demand_name)
            if rd is None:
                continue

            annual_mwh = float(getattr(rd, "value", getattr(rd, "annual", 0.0)))

            # choose target
            if self.min_mwh_by_region and rid in self.min_mwh_by_region:
                target = float(self.min_mwh_by_region[rid])
            elif self.min_share is not None:
                target = max(0.0, float(self.min_share)) * annual_mwh
            else:
                continue

            has_dhn = any(
                getattr(getattr(rt, "technology", None), "name", "") in self.dhn_tech_names
                for rt in getattr(region, "region_technologies", [])
            )
            if not has_dhn:
                continue

            targets[rid] = target

        constraints.setdefault("min_dhn_throughput_mwh", {}).update(targets)
        energy_system.constraints = constraints
        return energy_system
