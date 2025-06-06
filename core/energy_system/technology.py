from abc import ABC
from dataclasses import dataclass

import pandas as pd


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
                 availability_profile: pd.Series = None):
        self.name = name
        self.commodity_in = commodity_in
        self.commodity_out = commodity_out
        self.efficiency = efficiency
        self.technical_lifetime = technical_lifetime
        self.opex_cost_energy = opex_cost_energy
        self.opex_cost_power = opex_cost_power
        self.capex_cost_power = capex_cost_power
        self.availability_profile = availability_profile


class CHP(Technology):
    ...

@dataclass
class RegionTechnology:
    technology: Technology
    initial_residual_capacity: float = 0
    initial_energy_output: float  = 0
    output_profile: pd.Series | None = None
