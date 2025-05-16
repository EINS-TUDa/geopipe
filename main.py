# -*- coding: utf-8 -*-
from core.district import District
import geopandas as gpd



if __name__ == "__main__":
    gdf_bb = gpd.read_file("data/bounding_box_epsg3035.geojson")
    gdf_bb.set_crs("EPSG:3035", inplace=True, allow_override=True)
    district = District.from_bounding_box(1, gdf_bb)
    district.print_technology_shares()