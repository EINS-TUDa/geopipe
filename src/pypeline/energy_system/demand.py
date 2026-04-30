from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from pypeline.energy_system.units import UnitEnum


@dataclass(frozen=True)
class DemandType:
    name: str
    commodity_in: str
    cooperation_of_technologies: bool
    default_decentral_supply_technology: str
    technology_shares_query_params: dict[str, Any]
    decrease_percent_per_year: float
    demand_column_name: str
    profile_path: Path
    # TODO: Add unit attribute which automatically converts the demand value to the unit of the energy system if necessary.
    #  For now, we assume that the unit of the demand value is the same as the energy unit of the energy system.


class Demand:

    def __init__(self, demand_type: DemandType, value: float, profile: pd.Series, profile_name: str):
        """

        Parameters
        ----------
        demand_type
        value : float
            The value of the initial year
        profile : pd.Series
            A Series with numeric values. Consists of 8760 data points with the demand per hour
        profile_name : str
        decrease_percent_per_year :
            Decrease of the value per year in percent
        """
        self._demand_type = demand_type
        self._profile = profile / profile.sum()
        self._profile_name = profile_name
        self._value = value

    @property
    def demand_type(self) -> DemandType:
        return self._demand_type

    @property
    def name(self) -> str:
        return self._demand_type.name

    @property
    def profile(self) -> pd.Series:
        return self._profile

    @property
    def profile_name(self) -> str:
        return self._profile_name

    def value(self, year_period) -> float:
        """The value of the year after the start"""
        return self._value * (1 - self.demand_type.decrease_percent_per_year) ** year_period

    def values_per_year(self, years: Iterable[int]) -> dict[int, float]:
        """The value of the demand for each year in years"""
        return {year: self.value(year - min(years)) for year in years}

    def peak(self, year_period) -> float:
        return self.value(year_period) * self.profile.max()