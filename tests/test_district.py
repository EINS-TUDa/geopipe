from pathlib import Path

import geopandas as gpd
import pytest

from pypeline.data.datasets import (
    Census2022HeatingType100mGrid,
    CensusTechnology,
    WaermeatlasHessen,
)

def test_district_from_bounding_box():
    region = gpd.read_file(Path("examples/bensheim/wah_bensheim_4_districts.geojson"))
    cen = Census2022HeatingType100mGrid()
    shares = {
        tech: float(value)
        for tech, value in cen.query(
            {"region": region, "key": "residential_heat_technology_shares"}
        ).items()
    }

    counts = {
        CensusTechnology.Gas: 238.0,
        CensusTechnology.Oil: 32.0,
        CensusTechnology.Wood: 12.0,
        CensusTechnology.Biomass: 0.0,
        CensusTechnology.Renewable: 6.0,
        CensusTechnology.Electric: 0.0,
        CensusTechnology.Coal: 0.0,
        CensusTechnology.District_Heating: 0.0,
        CensusTechnology.NoEnergyCarrier: 3.0,
    }
    total_count = sum(counts.values())
    expected_shares = {tech: value / total_count for tech, value in counts.items()}

    assert pytest.approx(1.0, rel=1e-9) == sum(shares.values())
    for tech, expected in expected_shares.items():
        assert shares[tech] == pytest.approx(expected, rel=1e-9)


def test_residential_yearly_heat_demand():
    region = gpd.read_file(Path("examples/bensheim/baublock_bensheim_epsg25832.geojson"))
    wh = WaermeatlasHessen()
    total_heat_demand = wh.query({"region": region, "key": "residential_heat"})
    total_heat_demand = round(total_heat_demand, 2)
    correct_heat_demand = 226487.95
    assert total_heat_demand == correct_heat_demand, f"Expected {correct_heat_demand}, got {total_heat_demand}"
    print("Test successful: Residential yearly heat demand matches expected value.")
