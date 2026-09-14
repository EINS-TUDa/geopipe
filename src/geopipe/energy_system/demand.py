import math
from typing import Annotated, Iterable, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from geopipe.data.data_registry import DataRegistryQuery


#: A share constrained to the closed interval [0, 1].
Share = Annotated[float, Field(ge=0.0, le=1.0)]


class DemandType(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    commodity_in: str
    #: Annual demand per region, converted from the dataset's unit to the energy unit of the energy system.
    #: A None or zero value means the demand does not exist in that region.
    value: DataRegistryQuery
    profile: DataRegistryQuery
    cooperation_of_technologies: bool
    decrease_percent_per_year: float
    #: Existing decentral technology shares per region (e.g. census-derived). ``None`` for demands
    #: without such data (e.g. special demands), in which case
    #: ``default_decentral_supply_technology`` provides the existing mix.
    technology_shares: Optional[DataRegistryQuery] = None
    #: Fallback decentral supply when no census shares are available. Either a single
    #: technology name, or a ``(tech_name, share)`` mix whose shares sum to 1.
    default_decentral_supply_technology: str | list[tuple[str, Share]] | None = None

    @model_validator(mode="after")
    def _validate_default_supply(self) -> "DemandType":
        if isinstance(self.default_decentral_supply_technology, list):
            if not self.default_decentral_supply_technology:
                raise ValueError(
                    f"DemandType '{self.name}': default_decentral_supply_technology must not be an empty list.")
            total = sum(share for _, share in self.default_decentral_supply_technology)
            if not math.isclose(total, 1.0):
                raise ValueError(
                    f"DemandType '{self.name}': default_decentral_supply_technology shares "
                    f"sum to {total}, must be 1.0.")
        return self

    def default_supply_shares(self) -> dict[str, float]:
        """Normalize ``default_decentral_supply_technology`` to a ``{tech_name: share}`` dict.

        Returns an empty dict when no default is configured.
        """
        default = self.default_decentral_supply_technology
        if default is None:
            return {}
        if isinstance(default, str):
            return {default: 1.0}
        return {name: share for name, share in default}


class Demand:

    def __init__(self, demand_type: DemandType, value: float, profile: pd.Series):
        """

        Parameters
        ----------
        demand_type
        value : float
            The value of the initial year
        profile : pd.Series
            A Series with numeric values. Consists of 8760 data points with the demand per hour
        decrease_percent_per_year :
            Decrease of the value per year in percent
        """
        self._demand_type = demand_type
        self._profile = profile / profile.sum()
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

    def value(self, year_period) -> float:
        """The value of the year after the start"""
        return self._value * (1 - self.demand_type.decrease_percent_per_year) ** year_period

    def values_per_year(self, years: Iterable[int]) -> dict[int, float]:
        """The value of the demand for each year in years"""
        return {year: self.value(year - min(years)) for year in years}

    def peak(self, year_period) -> float:
        return self.value(year_period) * self.profile.max()
