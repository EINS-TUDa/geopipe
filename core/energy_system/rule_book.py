from abc import ABC

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.energy_system.region import Region


class RuleBook:
    def __init__(self):
        self.rules = []

    def add_rule(self, rule):
        """Adds a rule to the rulebook."""
        self.rules.append(rule)

    def get_rules(self):
        """Returns all rules in the rulebook."""
        return self.rules

    def apply(self, region) -> "Region":
        print("Rulenppl.apply - not implemented")
        return region

class Rule(ABC):
    """
    Possible rules can be:
    If high building density: HP is forbidden
    Certain technology only exists in certain regions
    """
    ...

class LimitTechnologyInRegionRule(Rule):
    def __init__(self, technology, condition):
        self.technology = technology
        self.condition = condition

    def apply(self, region):
        """Applies the rule to a given region."""
        if self.condition(region):
            region.restrict_technology(self.technology)