# -*- coding: utf-8 -*-
from core.district import District
import geopandas as gpd
from pathlib import Path
from tests.test_district import test_district_from_bounding_box
import core.datasets


def district_from_polygone():

    # gdf_bb = gpd.read_file(Path("data") / "baublock_bensheim_epsg25832.geojson")
    district = District.from_city_name_hessen(id_=1, city_name="Bensheim")
    district.print()

    # test_district_from_bounding_box()


if __name__ == "__main__":
    district_from_polygone()

