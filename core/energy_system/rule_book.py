from abc import ABC, abstractmethod

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.energy_system.region import Region


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
