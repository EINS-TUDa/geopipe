# -*- coding: utf-8 -*-
from pathlib import Path

from core.data.data_registry import DataRegistry
from core.data.datasets import ResidentialHeatDemandProfile
from core.energy_system.energy_system import EnergySystemBuilder
from core.energy_system.region import RegionBuilder
from core.energy_system.rule_book import EnergySystemRuleBook
from core.energy_system.technology_registry import TechnologyRegistry
from tests.test_district import test_district_from_bounding_box, test_residential_yearly_heat_demand

import geopandas as gpd

import core.energy_system.technologies

if __name__ == "__main__":
    technology_registry = TechnologyRegistry()
    technology_registry.load_from_default()

    data_registry = DataRegistry()
    data_registry.load_from_default()

    # polygon = gpd.read_file(Path("data") / "wah_bensheim_4_districts.geojson")
    polygon = gpd.read_file(Path("data") / "wah_1_polygon_without_census.geojson")

    # region_builder = RegionBuilder(technology_registry=technology_registry, data_registry=data_registry)
    # region = region_builder.build(polygon=polygon)

    esb = EnergySystemBuilder()
    esb.set_polygons(polygon)
    esb.set_default_technology_dependencies()
    esb.set_default_demands()
    esb.set_technology_registry(technology_registry)
    esb.set_data_registry(data_registry)


    es = esb.build()
    es.plot(demand_name="residential_heat", kind="energy")






    print("Finished")





