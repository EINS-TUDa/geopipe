from pathlib import Path
import geopandas as gpd
import pytest
from shapely.geometry import Polygon
import pandas as pd
from pypeline.energy_system.core import Demand, EnergySystem, Region, RegionDemand, Scenario
from pypeline.energy_technology.technology import RegionTechnology, Technology
from pypeline.optimization.cesm.input_writer import _write_cesm_inputs
from pypeline.optimization.resolved_system import resolve_system
from pypeline.units import UnitKW

REPO_ROOT = Path(__file__).resolve().parents[1]


DISTRICT_ID = 4


def _build_scenario() -> Scenario:
    return Scenario(name="Base", start_year=2020, end_year=2025, year_gap=5, discount_rate=0.05, lockout_years=0)


def _build_energy_system(data_dir=None, supply_prices=None) -> EnergySystem:
    demand = Demand(demand_type="residential_heat", commodity_in="residential_heat")
    region_demand = RegionDemand(demand=demand, value=100.0, profile=pd.Series([1.0 / 8760.0] * 8760))
    region = Region(id_=DISTRICT_ID, region_demands=[region_demand], region_technologies=[])
    return EnergySystem(
        name="test",
        regions=[region],
        units=UnitKW(),
        constraints={},
        data_dir=data_dir,
        grid_prices={"electricity": 120.0, "export": 0.0},
        supply_prices=supply_prices or {},
    )


def _single_polygon() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"id": DISTRICT_ID, "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}],
        geometry="geometry",
        crs="EPSG:3035",
    )


def _ensure_tss(workdir: Path) -> None:
    tss_source = REPO_ROOT / "CESM" / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    tss_target = workdir / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    tss_target.parent.mkdir(parents=True, exist_ok=True)
    tss_target.write_text(tss_source.read_text(encoding="utf-8"), encoding="utf-8")


def missing_supply_t(tmp_path: Path) -> None:
    """Checks for missing supply pricing inputs."""
    selected_techs = [
        Technology(
            f"ind_gas_boiler_D{DISTRICT_ID}",
            "gas",
            f"residential_heat_D{DISTRICT_ID}",
            cap_max=1000.0,
            max_units=10,
        )
    ]

    workdir = tmp_path / "cesm_strict_supply_price"
    _ensure_tss(workdir)

    es = _build_energy_system(data_dir=REPO_ROOT / "data", supply_prices={"oil": 80.0})
    resolved = resolve_system(es, _build_scenario(), retain_existing_output_schedule=[1.0, 0.95])
    with pytest.raises(ValueError, match="Missing supply price for commodity 'gas'"):
        _write_cesm_inputs(
            resolved,
            techmap_dir=workdir / "Data" / "Techmap",
            timeseries_dir=workdir / "Data" / "TimeSeries",
            model_name="StrictSupplyPrice",
            scenario_name="Base",
            tss_name="4ThinWeeks",
            dt_hours=1,
            selected_techs=selected_techs,
        )
