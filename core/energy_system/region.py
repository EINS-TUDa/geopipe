from core.data.data_registry import DataRegistry
from core.data.datasets import CensusTechnology
from core.energy_system.demand import Demand, RegionDemand
from core.energy_system.rule_book import RuleBook
import geopandas as gpd

from core.energy_system.technology import Technology, RegionTechnology, TechnologyDependencyManager, \
    TechnologyRequirement
from core.energy_system.technology_registry import TechnologyRegistry


class Region:
    def __init__(self,
                 id_: int,
                 polygon: gpd.GeoDataFrame,
                 region_demands: list["RegionDemand"],
                 region_technologies: list[RegionTechnology]):
        self.id = id_
        self.polygon = polygon
        self.region_demands = region_demands
        self.region_technologies = region_technologies

    def get_demand(self, name: str) -> RegionDemand | None:
        for demand in self.region_demands:
            if demand.demand.demand_type == name:
                return demand
        return None


class RegionBuilder:
    def __init__(self,
                 technology_registry: TechnologyRegistry,
                 data_registry: DataRegistry,
                 base_crs: str = "EPSG:25832", ):
        self.base_crs = base_crs
        self.rule_book = None
        self.data_registry = data_registry
        self.technology_registry = technology_registry
        self.demands = [
            Demand(demand_type="residential_heat",
                   commodity_in="residential_heat",
                   cooperation_of_technologies=False,
                   demand_query_params={"type": "residential_heat"},
                   profile_query_params={"type": "residential_heat_profile"},
                   technology_shares_query_params={"type": "residential_heat_technology_shares",
                                                   "name_mapping": {
                                                       CensusTechnology.Gas: "ind_gas_boiler",
                                                       CensusTechnology.Oil: "ind_oil_boiler",
                                                       CensusTechnology.Wood: "wood",
                                                       CensusTechnology.Biomass: None,
                                                       CensusTechnology.Renewable: "ind_heat_pump",
                                                       CensusTechnology.Electric: None,
                                                       CensusTechnology.Coal: None,
                                                       CensusTechnology.District_Heating: "ind_district_heating_connection",
                                                       CensusTechnology.NoEnergyCarrier: None}
                                                   }
                   ),
            Demand(demand_type="residential_electricity",
                   commodity_in="electricity",
                   cooperation_of_technologies=True,
                   demand_query_params={"type": "residential_electricity"},
                   profile_query_params={"type": "residential_electricity_profile"},
                   )
        ]
        self.technology_dependency_manager = None
        self.config = {
            "cap_factor_ind_technologies": 1.1 # Factor to increase the capacity of individual technologies over the minimum required capacity
        }



    def build_demands(self, polygon) -> list[RegionDemand]:
        collection = []
        for demand in self.demands:
            base_query = {"region": polygon, "base_crs": self.base_crs}

            profile = self.data_registry.query(demand.profile_query_params | base_query)
            if profile is None or profile.empty:
                raise ValueError(f"Demand profile for {demand.demand_type} not found in data registry.")

            demand_value = self.data_registry.query(demand.demand_query_params | base_query)
            if demand_value is None or not isinstance(demand_value, (int, float)):
                raise ValueError(f"Demand value for {demand.demand_type} not found or invalid in data registry.")

            # Create a RegionDemand instance
            region_demand = RegionDemand(
                demand=demand,
                value=demand_value,
                profile=profile
            )
            collection.append(region_demand)
        return collection

    def build_technologies(self, polygon: gpd.GeoDataFrame, r_demands: list[RegionDemand]) -> list[RegionTechnology]:
        collection = []
        technologies_with_shares = []
        all_technologies = self.technology_registry.get_all(return_type="name")
        # Technologies which supply demands
        for r_demand in r_demands:
            if r_demand.demand.technology_shares_query_params is not None:
                technologies_supplying_this_demand = (self.technology_registry.
                                                      get_by_output_commodity(commodity=r_demand.demand.commodity_in,
                                                                              return_type="name"))
                technologies_with_shares.extend(technologies_supplying_this_demand)

                base_query = {"region": polygon, "base_crs": self.base_crs}
                technology_shares_data = self.data_registry.query(
                    r_demand.demand.technology_shares_query_params | base_query)

                model_tech_shares = {
                    tech_name: share for tech_name, share in technology_shares_data.items()
                    if tech_name and tech_name in technologies_supplying_this_demand
                }

                total_share = sum(model_tech_shares.values())

                if total_share > 0:
                    normalized_shares = {tech: share / total_share for tech, share in model_tech_shares.items()}
                else:
                    normalized_shares = {tech: 0.0 for tech in model_tech_shares}

                for tech, share in normalized_shares.items():
                    initial_energy_output = r_demand.value * share
                    region_technology = RegionTechnology(
                        technology=self.technology_registry.get_by_name(tech),
                        initial_energy_output=initial_energy_output,
                        initial_capacity= max(r_demand.profile)*initial_energy_output * self.config["cap_factor_ind_technologies"],
                        output_profile=r_demand.profile,
                    )
                    collection.append(region_technology)

        # Add technologies that do not have shares defined in the data registry
        other_technologies = set(all_technologies) - set(technologies_with_shares)
        for tech_name in other_technologies:
            tech = self.technology_registry.get_by_name(tech_name)
            initial_output = 0.0
            initial_capacity = 0.0
            profile = None
            region_technology = RegionTechnology(
                technology=tech,
                initial_energy_output=initial_output,
                initial_capacity=initial_capacity,
                output_profile=profile
            )
            collection.append(region_technology)

        # Add Technology dependencies if available
        if self.technology_dependency_manager:
            tech_capacities = {r_tech.technology.name: r_tech.initial_capacity for r_tech in collection}

            for tech_name in list(tech_capacities.keys()):
                requirements = self.technology_dependency_manager.get_requirements(tech_name)

                for req in requirements:
                    required_capacity = tech_capacities[tech_name] * req.capacity_factor * req.share
                    tech_capacities[
                        req.technology_name] += required_capacity

            # Update the collection with the new capacities
            for r_tech in collection:
                tech_name = r_tech.technology.name
                if tech_name in tech_capacities:
                    r_tech.initial_capacity = tech_capacities[tech_name]

        return collection

    def build(self, polygon) -> Region:
        demands = self.build_demands(polygon)
        technologies = self.build_technologies(polygon, demands)

        region = Region(
            id_=polygon["id"],
            polygon=polygon,
            region_technologies=technologies,
            region_demands=demands
        )

        if self.rule_book:
            region = self.rule_book.apply(region)

        return region
