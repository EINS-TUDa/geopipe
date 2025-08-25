from pypeline.data.datasets import WaermeatlasHessen, Census2022HeatingType100mGrid
from pypeline.data.datasets import CensusTechnology
import geopandas as gpd
from pathlib import Path

def test_district_from_bounding_box():
    region = gpd.read_file(Path("data") / "test_bounding_box_epsg3035.geojson")
    cen = Census2022HeatingType100mGrid()
    shares = cen.query({"region": region}, "residential_heating_technology_shares")

    count_gas = 4+3+4+4
    count_oil = 3+9+6+7
    count_wood = 3
    count_el = 3
    total_count = count_gas + count_oil + count_wood + count_el

    expected_shares ={
        CensusTechnology.Gas: count_gas / total_count,
        CensusTechnology.Oil: count_oil / total_count,
        CensusTechnology.Wood: count_wood / total_count,
        CensusTechnology.Biomass: 0,
        CensusTechnology.Renewable: 0,
        CensusTechnology.Electric: count_el / total_count,
        CensusTechnology.Coal: 0,
        CensusTechnology.District_Heating: 0,
        CensusTechnology.NoEnergyCarrier: 0,
    }

    assert shares == expected_shares, "Technology shares do not match expected values."
    print("Test successful: District created with expected technology shares.")


def test_residential_yearly_heat_demand():
    region = gpd.read_file(Path("data") / "baublock_bensheim_epsg25832.geojson")
    wh = WaermeatlasHessen()
    total_heat_demand = wh.query({"region": region}, "residential_heat_demand")
    # round to 2 decimal places for comparison
    total_heat_demand = round(total_heat_demand, 2)
    correct_heat_demand = 226487.95
    assert total_heat_demand == correct_heat_demand, f"Expected {correct_heat_demand}, got {total_heat_demand}"
    print("Test successful: Residential yearly heat demand matches expected value.")
