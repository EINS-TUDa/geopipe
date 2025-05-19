# -*- coding: utf-8 -*-
from core.district import District
import geopandas as gpd
from pathlib import Path
from tests.test_district import test_district_from_bounding_box



if __name__ == "__main__":
    gdf_bb = gpd.read_file(Path("data") / "bounding_box_epsg3035.geojson")
    gdf_bb.set_crs("EPSG:3035", inplace=True, allow_override=True)
    district = District.from_bounding_box(1, gdf_bb)
    district.print_technology_shares()

    test_district_from_bounding_box()