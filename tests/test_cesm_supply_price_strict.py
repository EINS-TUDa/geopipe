from pathlib import Path
import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from pypeline.energy_technology.technology import Technology
from pypeline.optimization.om_adapter import OMContext
from tools.cesm_plugin import _write_cesm_inputs_from_om

REPO_ROOT = Path(__file__).resolve().parents[1]


DISTRICT_ID = 4


def _build_om() -> OMContext:
    years = [2020, 2025]
    return OMContext(
        years=years,
        regions=[DISTRICT_ID],
        commodity="residential_heat",
        annual_demand={DISTRICT_ID: {2020: 100.0, 2025: 100.0}},
        demand_profile=[1.0 / 8760.0] * 8760,
        schedules={},
        tss_indices=[],
        tss_weights=[],
        constraints={},
        technologies={},
        region_technology_metrics={},
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
    om = _build_om()
    polygons = _single_polygon()

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

    with pytest.raises(ValueError, match="Missing supply price for commodity 'gas'"):
        _write_cesm_inputs_from_om(
            om,
            workdir=workdir,
            model_name="StrictSupplyPrice",
            scenario_name="Base",
            tss_name="4ThinWeeks",
            polygons_gdf=polygons,
            data_dir=REPO_ROOT / "data",
            start_year=2020,
            end_year=2025,
            year_gap=5,
            discount_rate=0.05,
            dt_hours=1,
            pipe_loss_fraction=0.02,
            pipe_cap_max_mw=500.0,
            pipe_opex_eur_per_mwh=2.0,
            pipe_capex_eur_per_mw=30.0,
            pipe_lifetime_years=40,
            grid_prices={"electricity": 120.0, "export": 0.0},
            supply_prices={"oil": 80.0},
            selected_techs=selected_techs,
            retain_existing_output_schedule=[1.0, 0.95],
        )
