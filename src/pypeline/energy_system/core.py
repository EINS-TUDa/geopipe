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
from pypeline.energy_system.pipe import Pipe
from pypeline.units import Unit


@dataclass(slots=True)
class EnergySystem:
    name: str
    regions: list["Region"]
    units: Unit
    street_network: nx.Graph | None = None
    technology_registry: TechnologyRegistry | None = None
    grid_prices: dict[str, float] = field(default_factory=dict)
    supply_prices: dict[str, float] = field(default_factory=dict)
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


@dataclass
class RegionDemand:
    demand: Demand
    value: float | None = None
    profile: pd.Series | None = None

    def __post_init__(self) -> None:
        self.check_types()
        self.normalize_profile()

    def check_types(self) -> None:
        if not isinstance(self.demand, Demand):
            raise TypeError(f"demand must be a Demand instance, got {type(self.demand).__name__}")

        if self.value is not None:
            if not isinstance(self.value, (int, float)):
                raise TypeError(f"value must be numeric, got {type(self.value).__name__}")
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


Number = int | float


@dataclass
class Scenario:
    name: str
    start_year: int
    end_year: int
    year_gap: int
    tss: str = "4Times"
    co2_price: Number | dict[int, Number] | None = None
    co2_limit: Number | dict[int, Number] | None = None
    rules: list[str] = field(default_factory=list)
    discount_rate: float = 0.05
    retain_existing_output_schedule: list[float] | None = None
    retain_existing_output_drop_per_year: Number | None = None
    lockout_years: int = 0

    @property
    def years(self) -> list[int]:
        return list(range(self.start_year, self.end_year + 1, self.year_gap))

    @property
    def lockout_until_year(self) -> int:
        # new capacity only allowed after lockout_until_year
        return self.start_year + self.lockout_years


class Region:
    def __init__(self, id_: int, topology: nx.Graph | None = None, region_demands: list["RegionDemand"] | None = None, region_technologies: list[Any] | None = None, local_dhn_capex_base_eur: float | None = None):
        self.id = id_
        self.topology = topology if topology is not None else nx.Graph()
        self.region_demands = region_demands if region_demands is not None else []
        self.region_technologies = region_technologies if region_technologies is not None else []
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

    def get_demand(self, name: str) -> "RegionDemand | None":
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
