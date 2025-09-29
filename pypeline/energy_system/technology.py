from abc import ABC
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from .technology_stage import (
    TechnologyStage,
    TechnologyCategory,
    DEFAULT_STAGE,
    DEFAULT_CATEGORY,
)


class Technology(ABC):
    def __init__(self,
                 name: str,
                 commodity_in: str,
                 commodity_out: str,
                 efficiency: float = 1.0,
                 technical_lifetime: int = 100,
                 opex_cost_energy: float = 0,
                 opex_cost_power: float = 0,
                 capex_cost_power: float = 0,
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
        self.availability_profile = availability_profile
        # normalize to enums (allow passing raw strings for JSON flexibility)
        self.stage = TechnologyStage(stage) if not isinstance(stage, TechnologyStage) else stage
        self.category = TechnologyCategory(category) if not isinstance(category, TechnologyCategory) else category


class CHP(Technology):
    ...

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
