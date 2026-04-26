from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Iterable
import pandas as pd
from shapely.geometry import MultiPoint

from pypeline.energy_technology.technology import (Technology, CentralTechnology, DecentralTechnology,
                                                   CHPTechnology, GridTechnology)
from pypeline.topology_builder.topology import Topology


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


class Region:
    def __init__(self, id_: int, topology: Topology, demands: Iterable[Demand],
                 technologies: Iterable[Technology]):
        self._id = id_
        self._topology = topology
        self._demands = {demand.name: demand for demand in demands}
        self._technologies = {"decentral": [], "central": [], "chp": [], "grid": []}
        class_to_key = {DecentralTechnology: "decentral", CentralTechnology: "central", CHPTechnology: "chp",
                        GridTechnology: "grid"}
        for technology in technologies:
            self._technologies[class_to_key[type(technology)]].append(technology)

    @property
    def id(self) -> int:
        return self._id

    @property
    def topology(self) -> Topology:
        return self._topology

    @property
    def boundary(self):
        """Convex hull of topology nodes, usable as a polygon geometry for visualisation."""
        nodes = list(self._topology.graph.nodes)
        if not nodes:
            raise ValueError(f"Region {self.id} has no topology nodes")
        return MultiPoint(nodes).convex_hull

    @property
    def crs(self):
        return self._topology.graph.graph.get("crs")

    def demand(self, name: str) -> Optional[Demand]:
        return self._demands.get(name, None)

    @property
    def demands(self) -> tuple[Demand, ...]:
        return tuple(self._demands.values())

    @property
    def decentral_techs(self) -> tuple[DecentralTechnology, ...]:
        return tuple(self._technologies["decentral"])

    @property
    def central_techs(self) -> tuple[CentralTechnology, ...]:
        return tuple(self._technologies["central"])

    @property
    def chps(self) -> tuple[CHPTechnology, ...]:
        return tuple(self._technologies["chp"])

    @property
    def grids(self) -> tuple[GridTechnology, ...]:
        return tuple(self._technologies["grid"])
