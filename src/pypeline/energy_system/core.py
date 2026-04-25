"""Core domain models for the energy system.

Only owns pure data structures and light model behavior.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import networkx as nx
import pandas as pd
from shapely.geometry import MultiPoint

from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.energy_system.imports import Imports
from pypeline.energy_system.pipe import Pipe
from pypeline.units import Unit


@dataclass(slots=True)
class EnergySystem:
    name: str
    regions: list["Region"]
    units: Unit
    street_network: nx.Graph | None = None
    technology_registry: TechnologyRegistry | None = None
    imports: list[Imports] = field(default_factory=list)
    constraints: dict[str, dict[int, float]] = field(default_factory=dict)
    data_dir: str | Path | None = None
    pipes: list[Pipe] = field(default_factory=list)


@dataclass
class Demand:
    demand_type: str
    commodity_in: str
    cooperation_of_technologies: bool = True
    default_supply_technology: str | None = None
    demand_query_params: dict[str, Any] | None = None
    profile_query_params: dict[str, Any] | None = None
    technology_shares_query_params: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return f"{self.commodity_in}_Demand"


@dataclass
class RegionDemand:
    demand: Demand
    value: float | dict[int, float] | None
    profile: pd.Series
    profile_name: str

    @property
    def name(self) -> str:
        return self.demand.name

    def __post_init__(self) -> None:
        self.check_types()
        self.normalize_profile()

    def check_types(self) -> None:
        if not isinstance(self.demand, Demand):
            raise TypeError(f"demand must be a Demand instance, got {type(self.demand).__name__}")

        if self.value is not None and not isinstance(self.value, dict):
            if not isinstance(self.value, (int, float)):
                raise TypeError(f"value must be numeric or dict[int, float], got {type(self.value).__name__}")
            self.value = float(self.value)

        if self.profile is not None and not isinstance(self.profile, pd.Series):
            try:
                self.profile = pd.Series(self.profile)
            except Exception as exc:
                raise TypeError("profile must be a pandas Series or a sequence of numeric values") from exc

    def normalize_profile(self) -> None:
        if self.profile is None or self.profile.empty:
            return

        numeric_profile = pd.to_numeric(self.profile, errors="coerce")
        if numeric_profile.isna().any():
            raise ValueError("All values in the profile must be numeric.")

        total = float(numeric_profile.sum())
        if total <= 0.0:
            raise ValueError("Profile sum must be positive to normalize.")

        self.profile = numeric_profile / total

    def annual_value(self) -> float:
        return float(self.value) if self.value is not None else 0.0


@dataclass
class Scenario:
    name: str
    start_year: int
    end_year: int
    year_gap: int
    dt_hours: int
    tss: str
    co2_price: float | dict[int, float] | None = None
    co2_limit: float | dict[int, float] | None = None
    rules: list[str] = field(default_factory=list)
    discount_rate: float = 0.05
    retain_existing_output_schedule: list[float] | None = None
    retain_existing_output_drop_per_year: float | None = None
    lockout_years: int = 0

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.end_year + 1, self.year_gap))

    @property
    def lockout_until_year(self) -> int:
        # new capacity only allowed after lockout_until_year
        return self.start_year + self.lockout_years


class Region:
    def __init__(self, id_: int, topology: nx.Graph, region_demands: list["RegionDemand"],
                 region_technologies: list[Any],
                 local_dhn_capex_base_eur: float):
        self.id = id_
        self.topology = topology
        self.region_demands = region_demands
        self.region_technologies = region_technologies
        self.local_dhn_capex_base_eur = local_dhn_capex_base_eur

    @property
    def boundary(self):
        """Convex hull of topology nodes, usable as a polygon geometry for visualisation."""
        nodes = list(self.topology.nodes)
        if not nodes:
            raise ValueError(f"Region {self.id} has no topology nodes")
        return MultiPoint(nodes).convex_hull

    @property
    def crs(self):
        return self.topology.graph.get("crs")

    def get_demand(self, name: str) -> RegionDemand | None:
        for demand in self.region_demands:
            if demand.demand.demand_type == name:
                return demand
        return None


__all__ = [
    "Region",
    "Demand",
    "RegionDemand",
    "Scenario",
    "EnergySystem",
]
