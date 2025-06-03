from enum import Enum

from core.data.data_registry import DataRegistry
from core.data.technology import CensusTechnology
from core.energy_system.rule_book import RuleBook
import geopandas as gpd

from core.energy_system.technologies import IndividualTechnology, CentralTechnology, HeatGrid, GridConnection, Demand


class Region:
    def __init__(self,
                 id_: int,
                 polygon: gpd.GeoDataFrame,
                 individual_technologies: list[IndividualTechnology],
                 central_technologies: list[CentralTechnology],
                 heat_grids: list[HeatGrid],
                 grid_connections: list[GridConnection],
                 demands: list[Demand]):
        self.id = id_
        self.polygon = polygon
        self.individual_technologies = individual_technologies
        self.central_technologies = central_technologies
        self.heat_grids = heat_grids
        self.grid_connections = grid_connections
        self.demands = demands


class RegionBuilder:
    def __init__(self,
                 id_: int,
                 polygon: gpd.GeoDataFrame,
                 base_crs: str,
                 rule_book: RuleBook,
                 data_registry: DataRegistry,
                 technology_registry: TechnologyRegistry):
        self.id = id_
        self.polygon = polygon
        self.base_crs = base_crs
        self.rule_book = rule_book
        self.data_registry = data_registry
        self.technology_registry = technology_registry

    def build_demands(self) -> list[Demand]:
        # Placeholder for demand building logic
        return []

    def build(self) -> Region:
        demands = self.build_demands()
        ...