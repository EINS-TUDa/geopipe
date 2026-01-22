# -*- coding: utf-8 -*-
from pathlib import Path
import geopandas as gpd
from pypeline import DataRegistry, EnergySystemBuilder, TechnologyRegistry, EnergySystemRuleBook

project_root = Path(__file__).resolve().parents[2]


technology_registry = TechnologyRegistry()
technology_registry.load_from_default()

data_registry = DataRegistry()
data_registry.load_from_default()

polygons = gpd.read_file(project_root / "examples" / "bensheim" / "wah_bensheim_4_districts.geojson")
#polygons = gpd.read_file(project_root / "examples" / "bensheim" / "wah_1_polygon_without_census.geojson")

esb = EnergySystemBuilder()
esb.set_polygons(polygons)
esb.set_technology_dependency_manager(default=True)
esb.set_demands(default=True)
esb.set_technology_registry(technology_registry)
esb.set_data_registry(data_registry)

es = esb.build()
es.plot(demand_name="residential_heat", kind="energy")

print("Finished")


