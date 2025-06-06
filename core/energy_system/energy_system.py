import geopandas as gpd

from core.energy_system.region import Region, RegionBuilder
from core.energy_system.rule_book import RuleBook
from core.energy_system.technology import Demand
from core.energy_system.unit import Unit
from core.data.data_registry import DataRegistry


class EnergySystem:
    def __init__(self, name: str, regions: list[Region], units: Unit, connections: list):
        self.name = name
        self.regions = regions
        self.units = units
        self.connections = connections

class EnergySystemBuilder:
    def __init__(self, energy_system_name: str = "Default", base_crs: str = "EPSG:25832"):
        self.energy_system_name = energy_system_name
        self.base_crs = base_crs
        self.polygons = None
        self.region_builder = None
        self.rule_book = None
        self.data_registry = None
        self.technology_registry = None



    def set_polygons(self, polygons: gpd.GeoDataFrame):
        self.polygons = polygons.to_crs(self.base_crs)
        return self

    def set_rule_book(self, rule_book: RuleBook):
        self.rule_book = rule_book
        return self

    def set_data_registry(self, data_registry: DataRegistry):
        self.data_registry = data_registry
        return self

    def set_technology_registry(self, technology_registry: TechnologyRegistry):
        self.technology_registry = technology_registry
        return self

    def set_manual_demands(self, region_id, demand: Demand):
        ...

    def set_manual_individual_technology(self, region, technology: Technology):
        ...

    def build(self) -> EnergySystem:
        self.check_types()
        regions = []
        rb = RegionBuilder(
            base_crs=self.base_crs,
            rule_book=self.rule_book,
            data_registry=self.data_registry,
            technology_registry=self.technology_registry
        )
        for polygon in self.polygons:
            regions.append(rb.build(polygon=polygon))

        units = Unit()  # todo: Assuming Unit is a class that can be instantiated without parameters
        connections = []  # todo: Placeholder for connections, can be populated later

        return EnergySystem(
            name=self.energy_system_name,
            regions=regions,
            units=units,
            connections=connections
        )



    def check_types(self):
        if not isinstance(self.base_crs, str):
            raise ValueError("Base CRS must be set and a string")
        if not isinstance(self.polygons, gpd.GeoDataFrame):
            raise ValueError("Geometry must be set and a GeoDataFrame")
        if not isinstance(self.rule_book, RuleBook):
            raise ValueError("RuleBook must be set and of type RuleBook")
        if not isinstance(self.data_registry, DataRegistry):
            raise ValueError("DataRegistry must be set and of type DataRegistry")
        if not isinstance(self.technology_registry, TechnologyRegistry):
            raise ValueError("TechnologyRegistry must be set and of type TechnologyRegistry")

class JsonEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_json(self, json_file_path: str):
        self.polygons = gpd.read_file(json_file_path)
        self.polygons = self.polygons.to_crs(self.base_crs)

class NameEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_city_name(self, city_name: str):
        self.polygons = DataRegistry.fetch_data("GeoportalHessen", "CityBoundaries", {"city_name": city_name, "base_crs": self.base_crs})
        self.polygons = self.polygons.to_crs(self.base_crs)












