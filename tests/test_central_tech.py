from pathlib import Path
import sqlite3
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon
from cesm.core.input_parser import Parser
from cesm.core.model import Model
import pandas as pd
from pypeline.energy_system.core import Demand, EnergySystem, Region, RegionDemand, Scenario
from pypeline.energy_system.imports import Imports
from pypeline.energy_technology.technology import RegionTechnology, Technology
from pypeline.optimization.cesm.input_writer import _write_cesm_inputs
from pypeline.optimization.resolved_system import resolve_system
from pypeline.units import UnitKW

REPO_ROOT = Path(__file__).resolve().parents[1]


DISTRICT_ID = 0


def _dn(name: str) -> str:
    return f"{name}_D{DISTRICT_ID}"


def _peak_profile_value(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if isinstance(value, float) and pd.isna(value):
            return 0.0
        return float(value)
    raw = str(value).strip()
    if not raw:
        return 0.0
    if raw.startswith("[") and raw.endswith("]"):
        peak = 0.0
        for chunk in raw[1:-1].split(";"):
            parts = chunk.strip().split()
            if len(parts) >= 2:
                peak = max(peak, float(parts[1]))
        return peak
    return float(raw)


def _profile_value_for_year(value, year: int) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return 0.0
    if raw.startswith("[") and raw.endswith("]"):
        for chunk in raw[1:-1].split(";"):
            parts = chunk.strip().split()
            if len(parts) < 2:
                continue
            try:
                y = int(float(parts[0]))
                val = float(parts[1])
            except ValueError:
                continue
            if y == int(year):
                return val
        return 0.0
    return float(raw)


def _build_scenario(lockout_years: int = 0) -> Scenario:
    return Scenario(name="Base", start_year=2020, end_year=2030, year_gap=5, dt_hours=1, discount_rate=0.05, lockout_years=lockout_years)


def _build_energy_system(
    *,
    data_dir=None,
    constraints: dict | None = None,
    region_technologies: list | None = None,
) -> EnergySystem:
    demand = Demand(demand_type="residential_heat", commodity_in="residential_heat")
    region_demand = RegionDemand(demand=demand, value=1200.0, profile=pd.Series([1.0 / 8760.0] * 8760))
    region = Region(id_=DISTRICT_ID, region_demands=[region_demand], region_technologies=region_technologies or [])
    return EnergySystem(
        name="test",
        regions=[region],
        units=UnitKW(),
        constraints=constraints or {},
        data_dir=data_dir,
        imports=[
            Imports(commodity_out="electricity", price_eur_per_mwh=140.0),
            Imports(commodity_out="gas", price_eur_per_mwh=30.0),
        ],
    )


def _single_district_polygon() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {
                "id": DISTRICT_ID,
                "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                "street_length_m": 1000.0,
                "demand_street_length_m": 800.0,
                "nondemand_street_length_m": 200.0,
            }
        ],
        geometry="geometry",
        crs="EPSG:3035",
    )


def _ensure_tss(ts_dir: Path) -> None:
    tss_source = REPO_ROOT / "CESM" / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    ts_dir.mkdir(parents=True, exist_ok=True)
    (ts_dir / "4ThinWeeks.txt").write_text(tss_source.read_text(encoding="utf-8"), encoding="utf-8")


def _selected_techs() -> list[Technology]:
    return [
        Technology(
            "heat_pipe",
            "district_heat_out",
            "district_heat_in",
            efficiency=0.98,
            technical_lifetime=40,
            opex_cost_energy=0.0,
            pipe_capex_eur_per_km=1129000.0,
            capex_cost_power=50.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=100,
        ),
        Technology(
            "grid_electricity",
            "Dummy",
            "Electricity",
            efficiency=1.0,
            technical_lifetime=40,
            opex_cost_energy=140.0,
            capex_cost_power=0.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=1,
        ),
        Technology(
            "GasSupply",
            "Dummy",
            "gas",
            efficiency=1.0,
            technical_lifetime=40,
            opex_cost_energy=30.0,
            capex_cost_power=0.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=1,
        ),
        Technology(
            _dn("ind_gas_boiler"),
            "gas",
            "residential_heat",
            efficiency=0.95,
            technical_lifetime=25,
            opex_cost_energy=0.0,
            capex_cost_power=40.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=100,
        ),
        Technology(
            _dn("cen_heat_pump"),
            "Electricity",
            _dn("district_heat_in"),
            efficiency=2.2,
            technical_lifetime=25,
            opex_cost_energy=0.0,
            capex_cost_power=5000.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=100,
        ),
        Technology(
            _dn("heat_grid"),
            _dn("district_heat_in"),
            _dn("district_heat_out"),
            efficiency=0.9,
            technical_lifetime=40,
            opex_cost_energy=0.0,
            capex_cost_power=80.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=100,
        ),
        Technology(
            _dn("heat_exchanger"),
            _dn("district_heat_out"),
            "residential_heat",
            efficiency=0.95,
            technical_lifetime=40,
            opex_cost_energy=0.0,
            capex_cost_power=40.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=100,
        ),
    ]


def _run_case(*, tmp_path: Path, model_name: str, force_central_cap_mw: float | None) -> tuple[float, float, float, float, float]:
    workdir = tmp_path / model_name
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir = workdir / "Data" / "TimeSeries"

    _ensure_tss(ts_dir)

    constraints = {}
    if force_central_cap_mw and force_central_cap_mw > 0.0:
        constraints["min_central_cap_mw_total"] = {DISTRICT_ID: float(force_central_cap_mw)}

    es = _build_energy_system(data_dir=REPO_ROOT / "data", constraints=constraints)
    resolved = resolve_system(es, _build_scenario(), retain_existing_output_schedule=[1.0, 1.0, 1.0])
    _write_cesm_inputs(
        resolved,
        techmap_dir=techmap_dir,
        timeseries_dir=ts_dir,
        model_name=model_name,
        scenario_name="Base",
        tss_name="4ThinWeeks",
        heat_commodity_base="residential_heat",
        dt_hours=1,
        selected_techs=_selected_techs(),
    )

    xlsx = techmap_dir / f"{model_name}.xlsx"
    convsub = pd.read_excel(xlsx, sheet_name="ConversionSubProcess")
    cen_row = convsub[convsub["conversion_process_name"] == _dn("cen_heat_pump")]
    assert not cen_row.empty
    central_cap_min_floor = _peak_profile_value(cen_row.iloc[0].get("cap_min"))
    central_cap_res_min_floor = _peak_profile_value(cen_row.iloc[0].get("cap_res_min"))

    conn = sqlite3.connect(":memory:")
    parser = Parser(model_name, techmap_dir_path=techmap_dir, ts_dir_path=ts_dir, db_conn=conn, scenario="Base")
    parser.parse()
    model = Model(conn=conn)
    model.solve()
    assert model.model.SolCount > 0, f"CESM solve produced no feasible solution (status={model.model.Status})"
    model.save_output()

    cur = conn.cursor()
    totex = float(cur.execute("SELECT TOTEX FROM output_global LIMIT 1").fetchone()[0] or 0.0)
    central_cap_new = float(
        cur.execute(
            """
            SELECT COALESCE(SUM(o.cap_new), 0.0)
            FROM output_cs_y o
            JOIN conversion_subprocess cs ON cs.id = o.cs_id
            JOIN conversion_process cp ON cp.id = cs.cp_id
            WHERE cp.name LIKE 'cen_%'
            """
        ).fetchone()[0]
        or 0.0
    )
    central_cap_res = float(
        cur.execute(
            """
            SELECT COALESCE(SUM(o.cap_res), 0.0)
            FROM output_cs_y o
            JOIN conversion_subprocess cs ON cs.id = o.cs_id
            JOIN conversion_process cp ON cp.id = cs.cp_id
            WHERE cp.name LIKE 'cen_%'
            """
        ).fetchone()[0]
        or 0.0
    )
    conn.close()
    return totex, central_cap_new, central_cap_res, central_cap_min_floor, central_cap_res_min_floor


def forced_central_t(tmp_path: Path) -> None:
    """Checks forced central capacity remains feasible while basic cost behavior stays valid."""
    baseline_totex, baseline_central_cap_new, baseline_central_cap_res, baseline_central_floor, baseline_central_res_floor = _run_case(
        tmp_path=tmp_path,
        model_name="CentralTechBaseline",
        force_central_cap_mw=None,
    )
    forced_totex, forced_central_cap_new, forced_central_cap_res, forced_central_floor, forced_central_res_floor = _run_case(
        tmp_path=tmp_path,
        model_name="CentralTechForced",
        force_central_cap_mw=0.5,
    )

    assert baseline_central_floor <= 1e-9
    assert forced_central_floor > 0.0
    assert baseline_central_res_floor <= 1e-9
    assert forced_central_res_floor <= 1e-9
    assert baseline_central_cap_new <= 1e-9
    assert forced_central_cap_new >= baseline_central_cap_new
    assert baseline_central_cap_res <= 1e-9
    assert forced_central_cap_res <= 1e-9
    assert forced_totex >= 0.0
    assert baseline_totex >= 0.0


def legacy_dispatch_t(tmp_path: Path) -> None:
    """Checks retained legacy supply dispatches without requiring additional investment."""
    workdir = tmp_path / "LegacyDispatch"
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir = workdir / "Data" / "TimeSeries"

    _ensure_tss(ts_dir)

    es = _build_energy_system(
        data_dir=REPO_ROOT / "data",
        region_technologies=[
            RegionTechnology(Technology(_dn("ind_gas_boiler"), "gas", "residential_heat"), initial_capacity=1.0, initial_energy_output=1200.0),
        ],
    )
    resolved = resolve_system(es, _build_scenario(), retain_existing_output_schedule=[1.0, 1.0, 1.0])
    _write_cesm_inputs(
        resolved,
        techmap_dir=techmap_dir,
        timeseries_dir=ts_dir,
        model_name="LegacyDispatch",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        heat_commodity_base="residential_heat",
        dt_hours=1,
        selected_techs=_selected_techs(),
    )

    conn = sqlite3.connect(":memory:")
    parser = Parser("LegacyDispatch", techmap_dir_path=techmap_dir, ts_dir_path=ts_dir, db_conn=conn, scenario="Base")
    parser.parse()
    model = Model(conn=conn)
    model.solve()
    assert model.model.SolCount > 0, f"CESM solve produced no feasible solution (status={model.model.Status})"
    model.save_output()

    cur = conn.cursor()
    legacy_cap_res = float(
        cur.execute(
            """
            SELECT COALESCE(SUM(o.cap_res), 0.0)
            FROM output_cs_y o
            JOIN conversion_subprocess cs ON cs.id = o.cs_id
            JOIN conversion_process cp ON cp.id = cs.cp_id
            WHERE cp.name = ?
            """
            , (_dn("ind_gas_boiler"),)
        ).fetchone()[0]
        or 0.0
    )
    legacy_cap_new = float(
        cur.execute(
            """
            SELECT COALESCE(SUM(o.cap_new), 0.0)
            FROM output_cs_y o
            JOIN conversion_subprocess cs ON cs.id = o.cs_id
            JOIN conversion_process cp ON cp.id = cs.cp_id
            WHERE cp.name = ?
            """
            , (_dn("ind_gas_boiler"),)
        ).fetchone()[0]
        or 0.0
    )
    legacy_eout = float(
        cur.execute(
            """
            SELECT COALESCE(SUM(o.eouttot), 0.0)
            FROM output_cs_y o
            JOIN conversion_subprocess cs ON cs.id = o.cs_id
            JOIN conversion_process cp ON cp.id = cs.cp_id
            WHERE cp.name = ?
            """
            , (_dn("ind_gas_boiler"),)
        ).fetchone()[0]
        or 0.0
    )
    conn.close()

    assert legacy_cap_res > 0.0
    assert legacy_cap_new <= 1e-9
    assert legacy_eout > 0.0


def lockout_keeps_min_t(tmp_path: Path) -> None:
    """Checks lockout keeps retained central minimum output available in early years."""
    workdir = tmp_path / "CentralRetainLockout"
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir = workdir / "Data" / "TimeSeries"

    _ensure_tss(ts_dir)

    es = _build_energy_system(
        data_dir=REPO_ROOT / "data",
        region_technologies=[
            RegionTechnology(Technology(_dn("cen_heat_pump"), "Electricity", _dn("district_heat_in")), initial_capacity=2.0, initial_energy_output=300.0),
        ],
    )
    resolved = resolve_system(es, _build_scenario(lockout_years=2), retain_existing_output_schedule=[1.0, 0.95, 0.90])
    _write_cesm_inputs(
        resolved,
        techmap_dir=techmap_dir,
        timeseries_dir=ts_dir,
        model_name="CentralRetainLockout",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        heat_commodity_base="residential_heat",
        dt_hours=1,
        selected_techs=_selected_techs(),
    )

    xlsx = techmap_dir / "CentralRetainLockout.xlsx"
    convsub = pd.read_excel(xlsx, sheet_name="ConversionSubProcess")
    cen_row = convsub[convsub["conversion_process_name"] == _dn("cen_heat_pump")]
    assert not cen_row.empty

    min_eout_2020 = _profile_value_for_year(cen_row.iloc[0].get("min_eout"), 2020)
    cap_res_min_2020 = _profile_value_for_year(cen_row.iloc[0].get("cap_res_min"), 2020)
    cap_max_2020 = _profile_value_for_year(cen_row.iloc[0].get("cap_max"), 2020)

    assert cap_res_min_2020 > 0.0
    assert min_eout_2020 > 0.0
    assert cap_max_2020 <= 2.0 + 1e-9


def bad_retention_t(tmp_path: Path) -> None:
    """Checks invalid retention inputs fail when output exists without capacity."""
    workdir = tmp_path / "CentralBadMetrics"
    ts_dir = workdir / "Data" / "TimeSeries"

    _ensure_tss(ts_dir)

    es = _build_energy_system(
        data_dir=REPO_ROOT / "data",
        region_technologies=[
            RegionTechnology(Technology(_dn("cen_heat_pump"), "Electricity", _dn("district_heat_in")), initial_capacity=0.0, initial_energy_output=300.0),
        ],
    )
    resolved = resolve_system(es, _build_scenario(lockout_years=2), retain_existing_output_schedule=[1.0, 0.95, 0.90])
    with pytest.raises(ValueError, match="Invalid central retention metrics"):
        _write_cesm_inputs(
            resolved,
            techmap_dir=workdir / "Data" / "Techmap",
            timeseries_dir=ts_dir,
            model_name="CentralBadMetrics",
            scenario_name="Base",
            tss_name="4ThinWeeks",
            heat_commodity_base="residential_heat",
            dt_hours=1,
            selected_techs=_selected_techs(),
        )
