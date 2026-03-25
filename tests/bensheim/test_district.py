from pathlib import Path
import math
import geopandas as gpd
import pytest
from pypeline.data.default_registry import get_default_data_registry
from pypeline.data.dataset import CensusTechnology


REPO_ROOT = Path(__file__).resolve().parents[2]
HEAT_FILE = REPO_ROOT / "data/WaermeatlasHessen.gpkg"
SHARES_FILE = REPO_ROOT / "data/Census2022HeatingType100mGrid/Census2022HeatingType100mGrid_Polygons_southhessen.geojson"


def district_shares_t():
    """Checks heating share query returns a valid normalized nonnegative distribution."""
    region = gpd.read_file(REPO_ROOT / "examples/bensheim/wah_bensheim_4_districts.geojson")
    registry = get_default_data_registry(
        mode="local",
        local_heat_demand_file=HEAT_FILE,
        local_heating_shares_file=SHARES_FILE,
    )
    shares = {
        tech: float(value)
        for tech, value in registry.query({"region": region, "key": "heating_shares", "name_mapping": {}}).items()
    }

    required_technologies = {
        CensusTechnology.Gas,
        CensusTechnology.Oil,
        CensusTechnology.Wood,
        CensusTechnology.Biomass,
        CensusTechnology.Renewable,
        CensusTechnology.Electric,
        CensusTechnology.Coal,
        CensusTechnology.District_Heating,
        CensusTechnology.NoEnergyCarrier,
    }

    assert required_technologies.issubset(set(shares.keys()))
    assert pytest.approx(1.0, rel=1e-9) == sum(shares.values())
    for tech, value in shares.items():
        assert not math.isnan(value), f"Share for {tech} must be numeric"
        assert value >= 0.0, f"Share for {tech} must be nonnegative"
        assert value <= 1.0 + 1e-9, f"Share for {tech} must be <= 1"
    assert sum(value > 0.0 for value in shares.values()) >= 1, "At least one heating technology share must be positive"


def heat_demand_pos_t():
    """Checks residential heat demand query is positive, finite, and scales with larger region extent."""
    small_region = gpd.read_file(REPO_ROOT / "examples/bensheim/baublock_bensheim_epsg25832.geojson")
    large_region = gpd.read_file(REPO_ROOT / "examples/bensheim/wah_bensheim_4_districts.geojson")
    registry = get_default_data_registry(
        mode="local",
        local_heat_demand_file=HEAT_FILE,
        local_heating_shares_file=SHARES_FILE,
    )

    small_demand = float(registry.query({"region": small_region, "key": "residential_heat_demand"}))
    large_demand = float(registry.query({"region": large_region, "key": "residential_heat_demand"}))

    assert small_demand > 0.0
    assert large_demand > 0.0
    assert math.isfinite(small_demand)
    assert math.isfinite(large_demand)
    assert large_demand >= small_demand
