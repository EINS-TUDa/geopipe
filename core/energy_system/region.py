from dataclasses import dataclass
from typing import Callable

import pandas as pd

from core.data.data_registry import DataRegistry
from core.data.datasets import CensusTechnology
from core.energy_system.demand import Demand, RegionDemand
from core.energy_system.rule_book import RuleBook
import geopandas as gpd

from core.energy_system.technology import Technology, RegionTechnology
from core.energy_system.technology_registry import TechnologyRegistry


class Region:
    def __init__(self,
                 id_: int,
                 polygon: gpd.GeoDataFrame,
                 region_demands: list["RegionDemand"] ,
                 technologies: list[RegionTechnology]):
        self.id = id_
        self.polygon = polygon
        self.region_demands = region_demands
        self.region_technologies = technologies


class RegionBuilder:
    def __init__(self,
                 technology_registry: TechnologyRegistry,
                 data_registry: DataRegistry,
                 demands: list[Demand] = None,
                 rule_book: RuleBook = None,
                 base_crs: str = "EPSG:25832", ):
        self.base_crs = base_crs
        self.rule_book = rule_book
        self.data_registry = data_registry
        self.technology_registry = technology_registry
        self.demands = demands if demands else [
            Demand(type_="residential_heat", commodity_in="residential_heat", cooperation_of_technologies=False,
                   assigned_technology_shares="residential_heating_technology_shares"),
            Demand(type_="residential_electricity", commodity_in="electricity", cooperation_of_technologies=True)
        ]

        self.data_keys = {
            "residential_heat_technology_shares":
                {"name_mapping":
                    {
                        CensusTechnology.Gas: "ind_gas_boiler",
                        CensusTechnology.Oil: "ind_oil_boiler",
                        CensusTechnology.Wood: "wood",
                        CensusTechnology.Biomass: "",
                        CensusTechnology.Renewable: "ind_heat_pump",
                        CensusTechnology.Electric: "",
                        CensusTechnology.Coal: "",
                        CensusTechnology.District_Heating: "",
                        CensusTechnology.NoEnergyCarrier: ""
                    }
                }
        }
        """
        data keys: a nested dictionary of the form:
        {technology_name: {"initial_energy_output_key": str, "output_profile_key": str}}
        
        or for technology shares, where key and value are added to the query for data_type type_
        {type_: {key: value}}
        """

    def build_demands(self, polygon) -> list[RegionDemand]:
        collection = []
        for demand in self.demands:
            # Check if the demand has a profile in the data registry
            profile = self.data_registry.query({
                "type": demand.profile_name,
                "region": polygon,
                "base_crs": self.base_crs
            })
            if profile is None or profile.empty:
                raise ValueError(f"Demand profile for {demand.type_} not found in data registry.")

            demand_value = self.data_registry.query({
                "type": demand.type_,
                "region": polygon,
                "base_crs": self.base_crs
            })
            if demand_value is None or not isinstance(demand_value, (int, float)):
                raise ValueError(f"Demand value for {demand.type_} not found or invalid in data registry.")

            # Create a RegionDemand instance
            region_demand = RegionDemand(
                demand=demand,
                value=demand_value,
                profile=profile
            )
            collection.append(region_demand)
        return collection

    def determine_individual_technology_shares(self, polygon: gpd.GeoDataFrame, r_demand: RegionDemand,
                                               technologies_supplying_this_demand: list[str]) -> dict["str", float]:
        type_= r_demand.demand.type_+"_technology_shares"
        query = {
            "type": type_,
            "region": polygon,
            "base_crs": self.base_crs
        }

        # if there is an entry in self.data keys for this type, extend the query with all key value pais stored under this type
        if type_ in self.data_keys:
            for key, value in self.data_keys[type_].items():
                query[key] = value

        technology_shares = self.data_registry.query(query)

        # delete technologies in technology_shares that are not in technologies_supplying_this_demand
        technology_shares = {
            tech: share for tech, share in technology_shares.items()
            if tech in technologies_supplying_this_demand
        }

        # normalize the shares so that they sum to 1
        total_share = sum(technology_shares.values())
        if total_share == 0:
            # set all shares to 0
            technology_shares = {tech: 0.0 for tech in technology_shares}
        else:
            technology_shares = {tech: share / total_share for tech, share in technology_shares.items()}

        return technology_shares

    def build_technologies(self, polygon: gpd.GeoDataFrame, r_demands: list[RegionDemand]) -> list[RegionTechnology]:
        collection = []
        technologies_with_shares = []
        all_technologies = self.technology_registry.get_all(return_type="name")
        for r_demand in r_demands:
            if r_demand.demand.assigned_technology_shares is not None:
                technologies_supplying_this_demand = (self.technology_registry.
                                                      get_by_output_commodity(commodity=r_demand.demand.commodity_in,
                                                                              return_type="name"))
                technologies_with_shares.extend(technologies_supplying_this_demand)
                technology_shares = self.determine_individual_technology_shares(polygon, r_demand,
                                                                                technologies_supplying_this_demand)

                for tech, share in technology_shares.items():
                    region_technology = RegionTechnology(
                        technology=self.technology_registry.get_by_name(tech),
                        initial_energy_output=r_demand.value * share,
                        output_profile=r_demand.profile,
                    )
                    collection.append(region_technology)


        other_technologies = set(all_technologies) - set(technologies_with_shares)

        for tech_name in other_technologies:
            tech = self.technology_registry.get_by_name(tech_name)

            # Look up optional keys
            keys = self.data_keys.get(tech_name, {})

            # Initial energy output:
            output_key = keys.get("initial_energy_output_key")
            if output_key:
                initial_output = self.data_registry.query({
                        "type": output_key,
                        "region": polygon,
                        "base_crs": self.base_crs
                    })
            else:
                initial_output = 0.0

            # Output profile:
            profile_key = keys.get("output_profile_key")
            if profile_key:
                profile = self.data_registry.query({
                        "type": profile_key,
                        "region": polygon,
                        "base_crs": self.base_crs
                    })
            else:
                profile = None

            region_technology = RegionTechnology(
                technology=tech,
                initial_energy_output=initial_output,
                output_profile=profile
            )
            collection.append(region_technology)
        return collection

    def build(self, polygon) -> Region:
        demands = self.build_demands(polygon)
        technologies = self.build_technologies(polygon, demands)

        region = Region(
            id_=polygon["id"],
            polygon=polygon,
            technologies=technologies,
        )

        if self.rule_book:
            region = self.rule_book.apply(region)

        return region
