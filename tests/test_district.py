from core.district import District
import geopandas as gpd
from pathlib import Path
from core.technologies import Technologies

def test_district_from_bounding_box():
    bounding_box = gpd.read_file(Path("data") / "bounding_box_epsg3035.geojson")
    bounding_box.set_crs("EPSG:3035", inplace=True, allow_override=True)

    count_gas = 4+3+4+4
    count_oil = 3+9+6+7
    count_wood = 3
    count_el = 3
    total_count = count_gas + count_oil + count_wood + count_el

    expected_shares ={
        Technologies.Gas: count_gas / total_count,
        Technologies.Oil: count_oil / total_count,
        Technologies.Wood: count_wood / total_count,
        Technologies.Biomass: 0,
        Technologies.Renewable: 0,
        Technologies.Electric: count_el / total_count,
        Technologies.Coal: 0,
        Technologies.District_Heating: 0,
        Technologies.Other: 0,
        Technologies.NoEnergyCarrier: 0,
    }

    district = District.from_bounding_box(1, bounding_box)

    assert district.technology_shares == expected_shares
    print("District created successfully with expected technology shares.")