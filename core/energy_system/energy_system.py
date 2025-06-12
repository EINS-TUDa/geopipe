import geopandas as gpd
import pandas as pd

from core.energy_system.region import Region, RegionBuilder
from core.energy_system.region_connection import RegionConnection
from core.energy_system.rule_book import RuleBook
from core.energy_system.demand import Demand
from core.energy_system.unit import Unit, UnitEnum
from core.data.data_registry import DataRegistry
from core.energy_system.technology_registry import TechnologyRegistry
from core.energy_system.technology import Technology, RegionTechnology, TechnologyRequirement, \
    TechnologyDependencyManager


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
        self.connections = None
        self.unit = UnitEnum.GW.unit
        self.technology_dependency_manager = None

    def set_unit(self, input_unit: UnitEnum):
        self.unit = input_unit.unit

    def set_polygons(self, polygons: gpd.GeoDataFrame):
        if "id" not in polygons.columns:
            polygons = polygons.copy()
            polygons["id"] = range(len(polygons))
        else:
            # Ensure IDs are unique and fill in missing ones if needed
            if polygons["id"].isnull().any():
                polygons = polygons.copy()
                missing_ids = polygons["id"].isnull()
                max_existing_id = polygons["id"].dropna().max()
                next_id = 0 if pd.isna(max_existing_id) else int(max_existing_id) + 1
                polygons.loc[missing_ids, "id"] = range(next_id, next_id + missing_ids.sum())
        self.polygons = polygons.to_crs(self.base_crs)
        return self

    def set_rule_book(self, rule_book: RuleBook | None):
        self.rule_book = rule_book
        return self

    def set_data_registry(self, data_registry: DataRegistry):
        self.data_registry = data_registry
        return self

    def set_technology_registry(self, technology_registry: TechnologyRegistry):
        self.technology_registry = technology_registry
        return self

    def set_connections(self, connections: list[RegionConnection]):
        self.connections = connections

    def set_manual_demands(self, region_id, demand: Demand):
        ...

    def set_manual_individual_technology(self, region, technology: Technology):
        ...

    def build_connections(self) -> list[RegionConnection]:
        # Placeholder for building connections, can be implemented later
        return []

    def validate_district_heating_networks(self, energy_system: EnergySystem, min_threshold=0.0) -> EnergySystem:
        """Validate district heating, ensuring that the total district heating demand exceed the minimum threshold."""
        print("Function validate_district_heating_networks is only claude written - check")
        # 1. Nachbarregionen identifizieren
        region_neighbors = self._find_neighboring_regions(energy_system.regions)

        # 2. Regionen mit Fernwärme in Cluster gruppieren
        dh_clusters = self._group_district_heating_regions(energy_system.regions, region_neighbors)

        # 3. Für jeden Cluster die Gesamtwärmemenge berechnen
        cluster_total_heat = {}
        for cluster_id, regions in dh_clusters.items():
            total_heat = 0
            for region in regions:
                for r_tech in region.region_technologies:
                    if r_tech.technology.name == "ind_district_heating_connection":
                        total_heat += r_tech.initial_energy_output
            cluster_total_heat[cluster_id] = total_heat

        # 4. Fernwärme in kleinen Clustern entfernen
        for cluster_id, total_heat in cluster_total_heat.items():
            if total_heat < min_threshold:
                for region in dh_clusters[cluster_id]:
                    region.region_technologies = [
                        r_tech for r_tech in region.region_technologies
                        if r_tech.technology.name != "ind_district_heating_connection"]
        return energy_system

    @staticmethod
    def _find_neighboring_regions(regions):
        neighbors = {region.id: [] for region in regions}
        for i, region1 in enumerate(regions):
            for j, region2 in enumerate(regions):
                if i != j and region1.polygon.touches(region2.polygon).any():
                    neighbors[region1.id].append(region2.id)
        return neighbors

    def _group_district_heating_regions(self, regions, neighbors):
        # Regionen mit Fernwärme identifizieren
        dh_regions = {}
        for region in regions:
            if any(r_tech.technology.name == "ind_district_heating_connection"
                   for r_tech in region.region_technologies):
                dh_regions[region.id] = region

        # Cluster bilden mit Tiefensuche
        clusters = {}
        visited = set()
        cluster_id = 0

        for region_id in dh_regions:
            if region_id not in visited:
                cluster = []
                self._dfs_cluster(region_id, dh_regions, neighbors, visited, cluster)
                if cluster:
                    clusters[cluster_id] = [dh_regions[r_id] for r_id in cluster]
                    cluster_id += 1

        return clusters

    def _dfs_cluster(self, region_id, dh_regions, neighbors, visited, cluster):
        visited.add(region_id)
        cluster.append(region_id)

        for neighbor_id in neighbors[region_id]:
            if neighbor_id in dh_regions and neighbor_id not in visited:
                self._dfs_cluster(neighbor_id, dh_regions, neighbors, visited, cluster)

    def set_default_technology_dependencies(self):
        dependencies = {
            "ind_district_heating_connection": [
                TechnologyRequirement(technology_name="heat_grid", capacity_factor=1.5)
            ],
            "heat_grid": [
                TechnologyRequirement(technology_name="cen_heat_pump", share=0.5, capacity_factor=1.05),
                TechnologyRequirement(technology_name="cen_gas_boiler", share=0.5, capacity_factor=1.2)
            ]
        }
        self.technology_dependency_manager = TechnologyDependencyManager(dependencies=dependencies)



    def build(self) -> EnergySystem:
        self.check_types()
        regions = []
        rb = RegionBuilder(
            base_crs=self.base_crs,
            data_registry=self.data_registry,
            technology_registry=self.technology_registry
        )
        rb.technology_dependency_manager = self.technology_dependency_manager

        for row in self.polygons.itertuples(index=False):
            polygon = gpd.GeoDataFrame([{"geometry": row.geometry, "id": getattr(row, "id")}],
                                       crs=self.polygons.crs)
            regions.append(rb.build(polygon=polygon))

        if self.connections is None:
            connections = self.build_connections()
        else:
            connections = self.connections

        es = EnergySystem(
            name=self.energy_system_name,
            regions=regions,
            units=self.unit,
            connections=connections)

        #es = self.validate_district_heating_networks(es)

        return es

    def check_types(self):
        if not isinstance(self.base_crs, str):
            raise ValueError("Base CRS must be set and a string")
        if not isinstance(self.polygons, gpd.GeoDataFrame):
            raise ValueError("Geometry must be set and a GeoDataFrame")
        if not isinstance(self.rule_book, RuleBook | None):
            raise ValueError("RuleBook must be set and of type RuleBook or None")
        if not isinstance(self.data_registry, DataRegistry):
            raise ValueError("DataRegistry must be set and of type DataRegistry")
        if not isinstance(self.technology_registry, TechnologyRegistry):
            raise ValueError("TechnologyRegistry must be set and of type TechnologyRegistry")
        if not self.connections:
            print("Warning: No connections set, building default connections.")


class JsonEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_json(self, json_file_path: str):
        self.polygons = gpd.read_file(json_file_path)
        self.polygons = self.polygons.to_crs(self.base_crs)


class NameEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_city_name(self, city_name: str):
        self.polygons = DataRegistry.fetch_data("GeoportalHessen", "CityBoundaries",
                                                {"city_name": city_name, "base_crs": self.base_crs})
        self.polygons = self.polygons.to_crs(self.base_crs)
