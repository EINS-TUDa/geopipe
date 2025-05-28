from core.district import District
import geopandas as gpd
from pathlib import Path
from core.technology import Technology

def test_district_from_bounding_box():
    bounding_box = gpd.read_file(Path("data") / "bounding_box_epsg3035.geojson")
    bounding_box.set_crs("EPSG:3035", inplace=True, allow_override=True)

    count_gas = 4+3+4+4
    count_oil = 3+9+6+7
    count_wood = 3
    count_el = 3
    total_count = count_gas + count_oil + count_wood + count_el

    expected_shares ={
        Technology.Gas: count_gas / total_count,
        Technology.Oil: count_oil / total_count,
        Technology.Wood: count_wood / total_count,
        Technology.Biomass: 0,
        Technology.Renewable: 0,
        Technology.Electric: count_el / total_count,
        Technology.Coal: 0,
        Technology.District_Heating: 0,
        Technology.Other: 0,
        Technology.NoEnergyCarrier: 0,
    }

    district = District.from_bounding_box(1, bounding_box)

    assert district.technology_shares == expected_shares, "Technology shares do not match expected values."
    print("District created successfully with expected technology shares.")