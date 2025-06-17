# -*- coding: utf-8 -*-
from pathlib import Path

from core.data.data_registry import DataRegistry
from core.energy_system.energy_system import EnergySystemBuilder
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
    polygons = gpd.read_file(Path("data") / "wah_1_polygon_without_census.geojson")

    esb = EnergySystemBuilder()
    esb.set_polygons(polygons)
    esb.set_technology_dependency_manager(default=True)
    esb.set_demands(default=True)
    esb.set_technology_registry(technology_registry)
    esb.set_data_registry(data_registry)

    es = esb.build()
    es.plot(demand_name="residential_heat", kind="energy")

    print("Finished")





