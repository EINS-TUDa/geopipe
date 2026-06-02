import math
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Iterable

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from geopipe.topology_builder.topology import Topology


#: A share constrained to the closed interval [0, 1].
Share = Annotated[float, Field(ge=0.0, le=1.0)]


class DemandValueSource(BaseModel):
    """Resolves the annual demand value of a :class:`DemandType` in a given region.

    Subclasses implement :meth:`value_for_region`. Returning ``None`` (or a
    non-positive value) means the demand does not exist in that region, so the
    builder skips it there. This is what scopes a "special" demand to the
    regions where it is actually present.
    """

    model_config = ConfigDict(frozen=True)

    def value_for_region(self, region_id: int, topology: "Topology") -> float | None:
        raise NotImplementedError


class ColumnDemandValue(DemandValueSource):
    """Demand value = sum of an (extensive) geodata column over the region's edges.

    This is the classic, data-driven source: the value is distributed across the
    street network and summed per region. Regions where the column sums to zero
    (or is absent) get no demand.
    """

    column_name: str

    def value_for_region(self, region_id: int, topology: "Topology") -> float | None:
        values = [data.get(self.column_name, float("nan"))
                  for _, _, data in topology.graph.edges(data=True)]
        return float(np.nansum(values))


class ExplicitDemandValue(DemandValueSource):
    """Demand value taken from an explicit ``{region_id: value}`` mapping.

    The mapping keys double as the scope: the demand exists only in the listed
    regions. Intended for "special" demands whose value is known up front (and,
    later, can be swapped for a data-derived source without touching the builder).
    """

    value_per_region: dict[int, float]

    def value_for_region(self, region_id: int, topology: "Topology") -> float | None:
        return self.value_per_region.get(region_id)


class DemandType(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    commodity_in: str
    value_source: ColumnDemandValue | ExplicitDemandValue
    profile_path: Path
    cooperation_of_technologies: bool
    decrease_percent_per_year: float
    #: Query for census-derived decentral technology shares. ``None`` for demands
    #: without census data (e.g. special demands), in which case
    #: ``default_decentral_supply_technology`` provides the existing mix.
    technology_shares_query_params: dict[str, Any] | None = None
    #: Fallback decentral supply when no census shares are available. Either a single
    #: technology name, or a ``(tech_name, share)`` mix whose shares sum to 1.
    default_decentral_supply_technology: str | list[tuple[str, Share]] | None = None

    # TODO: Add unit attribute which automatically converts the demand value to the unit of the energy system if necessary.
    #  For now, we assume that the unit of the demand value is the same as the energy unit of the energy system.

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