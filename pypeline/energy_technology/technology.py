from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from .technology_stage import (
    TechnologyStage,
    TechnologyCategory,
    DEFAULT_STAGE,
    DEFAULT_CATEGORY,
)


class Technology:
    def __init__(self,
                 name: str,
                 commodity_in: str,
                 commodity_out: str,
                 efficiency: float = 1.0,
                 technical_lifetime: int = 100,
                 opex_cost_energy: float = 0,
                 opex_cost_power: float = 0,
                 capex_cost_power: float = 0,
                 capex_cost_base: float = 0,
                 cap_min: float | None = None,
                 cap_max: float | None = None,
                 max_units: int | None = None,
                 availability_profile: Optional[pd.Series] = None,
                 stage: TechnologyStage | str = DEFAULT_STAGE,
                 category: TechnologyCategory | str = DEFAULT_CATEGORY):
        self.name = name
        self.commodity_in = commodity_in
        self.commodity_out = commodity_out
        self.efficiency = efficiency
        self.technical_lifetime = technical_lifetime
        self.opex_cost_energy = opex_cost_energy
        self.opex_cost_power = opex_cost_power
        self.capex_cost_power = capex_cost_power
        self.capex_cost_base = capex_cost_base
        self.cap_min = cap_min
        self.cap_max = cap_max
        if max_units is None:
            self.max_units = None
        else:
            try:
                self.max_units = max(1, int(max_units))
            except Exception:
                self.max_units = None
        self.availability_profile = availability_profile
        self.stage = TechnologyStage(stage) if not isinstance(stage, TechnologyStage) else stage
        self.category = TechnologyCategory(category) if not isinstance(category, TechnologyCategory) else category

    def copy_with(self, **overrides) -> "Technology":
        """Return a new Technology with the same attributes overridden by kwargs."""
        payload = {
            "name": self.name,
            "commodity_in": self.commodity_in,
            "commodity_out": self.commodity_out,
            "efficiency": self.efficiency,
            "technical_lifetime": self.technical_lifetime,
            "opex_cost_energy": self.opex_cost_energy,
            "opex_cost_power": self.opex_cost_power,
            "capex_cost_power": self.capex_cost_power,
            "capex_cost_base": self.capex_cost_base,
            "cap_min": self.cap_min,
            "cap_max": self.cap_max,
            "max_units": self.max_units,
            "availability_profile": self.availability_profile,
            "stage": self.stage,
            "category": self.category,
        }
        payload.update(overrides)
        return Technology(**payload)

@dataclass
class RegionTechnology:
    technology: Technology
    initial_energy_output: float  = 0
    initial_capacity: float = 0
    output_profile: pd.Series | None = None


@dataclass
class TechnologyRequirement:
    technology_name: str
    capacity_factor: float = 1.0  # Capacity of this technology = capacity_factor * capacity of the demand
    share: float = 1.0  # share of capacity of dependency technology that is covered by this technology


@dataclass
class TechnologyDependencyManager:
    """Manages dependencies between technologies."""
    # Dictionary: technology_name -> List of required technologies
    dependencies: dict[str, list[TechnologyRequirement]] = field(default_factory=dict)

    def add_dependency(self, source_tech: str, requirement: TechnologyRequirement) -> None:
        if source_tech not in self.dependencies:
            self.dependencies[source_tech] = []
        self.dependencies[source_tech].append(requirement)

    def get_requirements(self, technology_name: str) -> list[TechnologyRequirement]:
        return self.dependencies.get(technology_name, [])
