from abc import ABC
from dataclasses import dataclass
import pandas as pd

from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_system.scenario import Scenario


class OptimizationModel(ABC):
    def __init__(self, conversion_sub_processes, conversion_processes, commodities, tss):
        self.conversion_sub_processes = ...
        self.conversion_processes = ...
        self.commodities = ...
        self.tss = ...

class Solution:
    def __init__(self):
        self.energy_system: EnergySystem
        self.scenario: Scenario
        self.results: Results

@dataclass
class Results:
    active_capacities: pd.DataFrame  # Columns: year, region, technology, capacity
    yearly_energy_outputs: pd.DataFrame       # Columns: year, region, technology, energy_output
    opex: float
    capex: float
    totex: float
