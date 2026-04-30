from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from ..energy_system import EnergySystem, Scenario


@dataclass
class Results:
    active_capacities: pd.DataFrame  # Columns: year, technology, region_id, capacity
    yearly_energy_outputs: pd.DataFrame  # Columns: year, technology, region_id, energy_output
    opex: float
    capex: float
    totex: float


@dataclass
class Solution:
    energy_system: Optional[EnergySystem] = field(default=None)
    scenario: Optional[Scenario] = field(default=None)
    results: Optional[Results] = field(default=None)


class OptimizationBackend(ABC):
    @abstractmethod
    def solve(self, energy_system: EnergySystem, scenario: Scenario) -> Solution:
        """Run the optimization and return a Solution."""
        ...
