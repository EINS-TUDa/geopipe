# -*- coding: utf-8 -*-
from pathlib import Path
import matplotlib.pyplot as plt
import geopandas as gpd
from core.data.datasets import WaermeatlasHessen, Census2022HeatingType100mGrid, GeoportalHessenCityBoundaries
from tests.test_district import test_district_from_bounding_box, test_residential_yearly_heat_demand

if __name__ == "__main__":
    city_boundary = GeoportalHessenCityBoundaries().query({"city_name": "Bensheim", "base_crs": "25832"}, "city_boundary")
    # plot city_boundary
    city_boundary.plot()
    plt.show()



