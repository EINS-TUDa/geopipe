from core.district import District
import geopandas as gpd
from pathlib import Path
from core.technology import Technology

def test_district_from_bounding_box():
    bounding_box = gpd.read_file(Path("data") / "test_bounding_box_epsg3035.geojson")

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
        Technology.NoEnergyCarrier: 0,
    }

    district = District.from_polygone(1, bounding_box)

    assert district.technology_shares == expected_shares, "Technology shares do not match expected values."
    print("Test successful: District created with expected technology shares.")


def test_residential_yearly_heat_demand():
    bounding_box = gpd.read_file(Path("data") / "baublock_bensheim_epsg25832.geojson")
    district = District.from_polygone(1, bounding_box)
    correct_heat_demand = 226487.95
    assert district.residential_yearly_heat_demand == correct_heat_demand, f"Expected {correct_heat_demand}, got {district.residential_yearly_heat_demand}"
    print("Test successful: Residential yearly heat demand matches expected value.")