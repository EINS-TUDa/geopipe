from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from pypeline.energy_system.core import EnergySystem, Scenario


@dataclass
class Results:
    active_capacities: pd.DataFrame  # Columns: year, technology, capacity
    yearly_energy_outputs: pd.DataFrame  # Columns: year, technology, energy_output
    opex: Optional[float]
    capex: Optional[float]
    totex: Optional[float]


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
