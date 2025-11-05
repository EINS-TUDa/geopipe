# -*- coding: utf-8 -*-
from pathlib import Path
from pypeline import DataRegistry, EnergySystemBuilder, TechnologyRegistry, EnergySystemRuleBook
import geopandas as gpd


technology_registry = TechnologyRegistry()
technology_registry.load_from_default()

data_registry = DataRegistry()
data_registry.load_from_default()

polygons = gpd.read_file(Path("data") / "wah_bensheim_4_districts.geojson")
#polygons = gpd.read_file(Path("data") / "wah_1_polygon_without_census.geojson")

esb = EnergySystemBuilder()
esb.set_polygons(polygons)
esb.set_technology_dependency_manager(default=True)
esb.set_demands(default=True)
esb.set_technology_registry(technology_registry)
esb.set_data_registry(data_registry)

es = esb.build()
es.plot(demand_name="residential_heat", kind="energy")

print("Finished")


