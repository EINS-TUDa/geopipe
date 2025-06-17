import geopandas as gpd
import pandas as pd

from core.energy_system.region import Region, RegionBuilder
from core.energy_system.region_connection import RegionConnection
from core.energy_system.rule_book import RegionRuleBook, EnergySystemRuleBook
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

    def plot(self, demand_name: str | None = None, kind: str = "energy"):
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
        from shapely.geometry import Point
        import matplotlib.cm as cm

        if kind not in ["energy", "power"]:
            raise ValueError("kind must be either 'energy' or 'power'")

        # Create a figure and axis
        fig, ax = plt.subplots(figsize=(10, 10))

        all_technologies = {r_tech.technology.name for region in self.regions for r_tech in region.region_technologies}
        # sort alphabetically by key
        all_technologies = sorted(all_technologies)
        color_map = {tech: color for tech, color in zip(all_technologies, cm.Set2.colors)}

        # Plot each region's polygon
        for region in self.regions:
            region.polygon.boundary.plot(ax=ax, edgecolor="black", zorder=1)
            region.polygon.plot(ax=ax, alpha=0.3, color="grey")

            # If demand_name is provided, calculate the pie chart data
            if demand_name:
                demand = region.get_demand(demand_name)
                if demand:
                    if kind == "energy":
                        tech_outputs = {
                            r_tech.technology.name: r_tech.initial_energy_output
                            for r_tech in region.region_technologies
                            if r_tech.technology.commodity_out == demand.demand.commodity_in
                        }
                    elif kind == "power":
                        tech_outputs = {
                            r_tech.technology.name: r_tech.initial_capacity
                            for r_tech in region.region_technologies
                            if r_tech.technology.commodity_out == demand.demand.commodity_in
                        }

                    # Normalize the outputs for the pie chart
                    total_output = sum(tech_outputs.values())
                    if total_output > 0:
                        tech_outputs = {k: v / total_output for k, v in tech_outputs.items()}

                    # Get the centroid of the polygon for pie chart placement
                    centroid = region.polygon.geometry.iloc[0].centroid
                    if isinstance(centroid, Point):
                        x, y = centroid.x, centroid.y

                        # Add the pie chart
                        sizes = list(tech_outputs.values())
                        colors = [color_map[tech] for tech in tech_outputs.keys()]
                        pie_radius = 20 + total_output / 100000  # Adjust the radius based on total demand
                        wedge, _ = ax.pie(
                            sizes,
                            colors=colors,
                            radius=pie_radius,
                            center=(x, y),
                            frame=True,
                            textprops={"fontsize": 6},
                            wedgeprops={"width": pie_radius / 1.5, 'linewidth': 2, 'edgecolor': 'white'}
                        )

                        [w.set_zorder(2) for w in wedge]

                        # Add the total demand as text below the pie chart
                        if kind =="energy":
                            ax.text(
                                x, y - pie_radius - 10,  # Position the text below the pie chart
                                f"Total: {total_output / 1000:.0f} MWh ",
                                ha="center",
                                fontsize=10,
                                zorder=3
                            )
                        elif kind == "power":
                            ax.text(
                                x, y - pie_radius - 10,  # Position the text below the pie chart
                                f"Total: {total_output:.2f} kW",
                                ha="center",
                                fontsize=10,
                                zorder=3
                            )

        # Add a legend for the technologies
        legend_elements = [Patch(facecolor=color, label=tech) for tech, color in color_map.items()]
        ax.legend(handles=legend_elements, loc="upper right", title="Technologies")

        # Set axis labels and title
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title(
            f"Energy System: {self.name} - Demand: {demand_name}" if demand_name else f"Energy System: {self.name}")

        # Show the plot
        plt.show()


class EnergySystemBuilder:
    def __init__(self, energy_system_name: str = "Default", base_crs: str = "EPSG:25832"):
        self.energy_system_name = energy_system_name
        self.base_crs = base_crs
        self.polygons = None
        self.region_builder = None
        self.rule_book = None
        self.region_rule_book = None
        self.data_registry = None
        self.technology_registry = None
        self.connections = None
        self.unit = UnitEnum.GW.unit
        self.technology_dependency_manager = None
        self.region_builder_config = None

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

    def set_region_rule_book(self, rule_book: RegionRuleBook):
        self.region_rule_book = rule_book
        return self

    def set_energy_system_rule_book(self, rule_book: EnergySystemRuleBook):
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

    def build_connections(self) -> list[RegionConnection]:
        # Placeholder for building connections, can be implemented later
        return []

    def set_default_technology_dependencies(self):
        dependencies = {
            "ind_district_heating_connection": [
                TechnologyRequirement(technology_name="heat_grid", capacity_factor=1.5)
            ],
            "heat_grid": [
                TechnologyRequirement(technology_name="cen_heat_pump", share=0.5, capacity_factor=1.05)
            ]
        }
        self.technology_dependency_manager = TechnologyDependencyManager(dependencies=dependencies)

    def set_default_region_builder_config(self):
        self.region_builder_config = {
            "cap_factor_ind_technologies": 1.1,  # Factor to increase the capacity of individual technologies over the minimum required capacity
        }
        return self

    def build(self) -> EnergySystem:
        # verify the required attributes are set
        self.verify()

        # setup region builder
        rb = RegionBuilder(
            base_crs=self.base_crs,
            data_registry=self.data_registry,
            technology_registry=self.technology_registry)
        rb.set_technology_dependency_manager(self.technology_dependency_manager)
        if self.region_rule_book:
            rb.set_rule_book(self.region_rule_book)
        if not self.region_builder_config:
            self.set_default_region_builder_config()
        rb.set_config(self.region_builder_config)

        # build regions from polygons
        regions = []
        for row in self.polygons.itertuples(index=False):
            polygon = gpd.GeoDataFrame([{"geometry": row.geometry, "id": getattr(row, "id")}],
                                       crs=self.polygons.crs)
            regions.append(rb.build(polygon=polygon))

        # build connections if not provided
        if self.connections is None:
            connections = self.build_connections()
        else:
            connections = self.connections

        # create the energy system
        es = EnergySystem(
            name=self.energy_system_name,
            regions=regions,
            units=self.unit,
            connections=connections)

        # apply energy system rule book if set
        if self.rule_book:
            es = self.rule_book.apply(es)

        return es

    def verify(self):
        if not isinstance(self.base_crs, str):
            raise ValueError(f"Base CRS must be set and a string and not {type(self.base_crs)}")
        if not isinstance(self.polygons, gpd.GeoDataFrame):
            raise ValueError(f"Geometry must be set and a GeoDataFrame and not {type(self.polygons)}")
        if not isinstance(self.rule_book, EnergySystemRuleBook | None):
            raise ValueError(f"RuleBook must be set and of type RuleBook or None and not {type(self.rule_book)}")
        if not isinstance(self.data_registry, DataRegistry):
            raise ValueError(f"DataRegistry must be set and of type DataRegistry and not {type(self.data_registry)}")
        if not isinstance(self.technology_registry, TechnologyRegistry):
            raise ValueError(
                f"TechnologyRegistry must be set and of type TechnologyRegistry and not {type(self.technology_registry)}")
        if not self.connections:
            print("Warning: No connections set, building default connections.")
        # Technology Dependency Manager
        if self.technology_dependency_manager:
            for key, value in self.technology_dependency_manager.dependencies.items():
                if not self.technology_registry.has_technology(key):
                    raise ValueError(
                        f"Technology {key} in the TechnologyDependencyManager is not registered in the TechnologyRegistry.")
                for requirement in value:
                    if not self.technology_registry.has_technology(requirement.technology_name):
                        raise ValueError(
                            f"Technology {requirement.technology_name} in the TechnologyDependencyManager of {key} is not registered in the TechnologyRegistry.")


class JsonEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_json(self, json_file_path: str):
        self.polygons = gpd.read_file(json_file_path)
        self.polygons = self.polygons.to_crs(self.base_crs)


class NameEnergySystemBuilder(EnergySystemBuilder):
    def set_geometry_from_city_name(self, city_name: str):
        self.polygons = DataRegistry.fetch_data("GeoportalHessen", "CityBoundaries",
                                                {"city_name": city_name, "base_crs": self.base_crs})
        self.polygons = self.polygons.to_crs(self.base_crs)
