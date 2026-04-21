from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from pypeline.energy_technology.technology import Technology
from pypeline.optimization.optimization_context import OptimizationContext
from pypeline.optimization.cesm.input_writer import _write_cesm_inputs_from_optimization_context


REPO_ROOT = Path(__file__).resolve().parents[1]


def _profile_peak(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return None
    if not raw.startswith("[") or not raw.endswith("]"):
        try:
            return float(raw)
        except ValueError:
            return None
    peaks = []
    for chunk in raw[1:-1].split(";"):
        parts = chunk.strip().split()
        if len(parts) < 2:
            continue
        try:
            peaks.append(float(parts[1]))
        except ValueError:
            continue
    return max(peaks) if peaks else None


def _ensure_tss(workdir: Path) -> None:
    tss_source = REPO_ROOT / "CESM" / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    tss_target = workdir / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    tss_target.parent.mkdir(parents=True, exist_ok=True)
    tss_target.write_text(tss_source.read_text(encoding="utf-8"), encoding="utf-8")


def _single_polygon() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [{"id": 0, "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}],
        geometry="geometry",
        crs="EPSG:3035",
    )


def _two_polygons() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        [
            {"id": 0, "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])},
            {"id": 1, "geometry": Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])},
        ],
        geometry="geometry",
        crs="EPSG:3035",
    )


def _mock_om() -> OptimizationContext:
    years = [2020, 2025, 2030]
    return OptimizationContext(
        years=years,
        regions=[0],
        commodity="residential_heat",
        annual_demand={0: {year: 1200.0 for year in years}},
        demand_profile=[1.0 / 8760.0] * 8760,
        schedules={},
        tss_indices=[],
        tss_weights=[],
        constraints={},
        technologies={},
        region_technology_metrics={
            0: {
                "heat_grid_D0": {"initial_capacity": 80.0, "initial_energy_output": 1200.0},
                "heat_exchanger_D0": {"initial_capacity": 70.0, "initial_energy_output": 0.0},
                "cen_heat_pump_D0": {"initial_capacity": 60.0, "initial_energy_output": 1500.0},
            }
        },
    )


def _selected_techs() -> list[Technology]:
    return [
        Technology("heat_grid_D0", "district_heat_in_D0", "district_heat_out_D0", cap_max=100.0, max_units=10),
        Technology("heat_exchanger_D0", "district_heat_out_D0", "residential_heat_D0", cap_max=30.0, max_units=10),
        Technology(
            "cen_heat_pump_D0",
            "electricity",
            "district_heat_in_D0",
            cap_min=0.2,
            cap_max=20.0,
            max_units=10,
        ),
    ]


def _mock_two_district_om() -> OptimizationContext:
    years = [2020, 2025]
    return OptimizationContext(
        years=years,
        regions=[0, 1],
        commodity="residential_heat",
        annual_demand={
            0: {year: 1200.0 for year in years},
            1: {year: 1200.0 for year in years},
        },
        demand_profile=[1.0 / 8760.0] * 8760,
        schedules={},
        tss_indices=[],
        tss_weights=[],
        constraints={},
        technologies={},
        region_technology_metrics={
            0: {
                "heat_grid_D0": {"initial_capacity": 80.0, "initial_energy_output": 1200.0},
                "heat_exchanger_D0": {"initial_capacity": 70.0, "initial_energy_output": 220.0},
                "cen_heat_pump_D0": {"initial_capacity": 60.0, "initial_energy_output": 1500.0},
            },
            1: {
                "heat_grid_D1": {"initial_capacity": 80.0, "initial_energy_output": 1200.0},
                "heat_exchanger_D1": {"initial_capacity": 70.0, "initial_energy_output": 0.0},
                "cen_heat_pump_D1": {"initial_capacity": 60.0, "initial_energy_output": 1500.0},
            },
        },
    )


def _selected_techs_two_district() -> list[Technology]:
    return [
        Technology("heat_grid_D0", "district_heat_in_D0", "district_heat_out_D0", cap_max=100.0, max_units=10),
        Technology("heat_grid_D1", "district_heat_in_D1", "district_heat_out_D1", cap_max=100.0, max_units=10),
        Technology("heat_exchanger_D0", "district_heat_out_D0", "residential_heat_D0", cap_max=30.0, max_units=10),
        Technology("heat_exchanger_D1", "district_heat_out_D1", "residential_heat_D1", cap_max=30.0, max_units=10),
        Technology("cen_heat_pump_D0", "electricity", "district_heat_in_D0", cap_min=0.2, cap_max=20.0, max_units=10),
        Technology("cen_heat_pump_D1", "electricity", "district_heat_in_D1", cap_min=0.2, cap_max=20.0, max_units=10),
    ]


def cap_bounds_synth_t(tmp_path):
    """Checks generated reserve and minimum capacity bounds are always clamped by cap_max."""
    workdir = tmp_path / "cesm_cap_bounds"
    _ensure_tss(workdir)

    _write_cesm_inputs_from_optimization_context(
        _mock_om(),
        workdir=workdir,
        model_name="SyntheticCapBounds",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=_single_polygon(),
        data_dir=REPO_ROOT / "data",
        start_year=2020,
        end_year=2030,
        year_gap=5,
        discount_rate=0.05,
        lockout_years=0,
        dt_hours=1,
        elec_price_eur_per_mwh=120.0,
        export_price_eur_per_mwh=0.0,
        grid_prices={"electricity": 120.0, "export": 0.0},
        supply_prices={"gas": 60.0},
        selected_techs=_selected_techs(),
        retain_existing_output_schedule=[1.0, 1.0, 1.0],
    )

    xlsx_path = workdir / "Data" / "Techmap" / "SyntheticCapBounds.xlsx"
    assert xlsx_path.exists()

    df = pd.read_excel(xlsx_path, sheet_name="ConversionSubProcess")
    assert not df.empty

    saw_reserve = False
    for _, row in df.iterrows():
        cap_max_peak = _profile_peak(row.get("cap_max"))
        if cap_max_peak is None:
            continue
        max_units_val = row.get("max_units")
        try:
            max_units = float(max_units_val) if max_units_val is not None else 1.0
        except (TypeError, ValueError):
            max_units = 1.0
        if not max_units or max_units < 1.0:
            max_units = 1.0
        total_cap_limit = cap_max_peak * max_units

        cap_min_peak = _profile_peak(row.get("cap_min"))
        if cap_min_peak is not None:
            assert cap_min_peak <= cap_max_peak + 1e-6

        for key in ("cap_res_min", "cap_res_max"):
            peak = _profile_peak(row.get(key))
            if peak is None:
                continue
            if peak > 0.0:
                saw_reserve = True
            assert peak <= total_cap_limit + 1e-6

    assert saw_reserve, "Expected at least one positive reserve bound in synthetic retained-capacity setup"


def obsolete_pipe_share_constraint_t(tmp_path):
    """Checks obsolete pipe-share constraint is rejected with migration guidance."""
    workdir = tmp_path / "cesm_obsolete_pipe_share"
    _ensure_tss(workdir)

    om = _mock_om()
    om.constraints = {"min_pipe_import_share_by_region": {0: 0.2}}

    with pytest.raises(ValueError, match="min_pipe_import_share_by_region"):
        _write_cesm_inputs_from_optimization_context(
            om,
            workdir=workdir,
            model_name="ObsoletePipeShare",
            scenario_name="Base",
            tss_name="4ThinWeeks",
            polygons_gdf=_single_polygon(),
            data_dir=REPO_ROOT / "data",
            start_year=2020,
            end_year=2030,
            year_gap=5,
            discount_rate=0.05,
            lockout_years=0,
            dt_hours=1,
            elec_price_eur_per_mwh=120.0,
            export_price_eur_per_mwh=0.0,
            grid_prices={"electricity": 120.0, "export": 0.0},
            supply_prices={"gas": 60.0},
            selected_techs=_selected_techs(),
            retain_existing_output_schedule=[1.0, 1.0, 1.0],
        )


def exchanger_target_not_pipe_floor_t(tmp_path):
    """Checks no pipe min floors and non-historical districts remain DHN-buildable."""
    workdir = tmp_path / "cesm_exchanger_target"
    _ensure_tss(workdir)

    _write_cesm_inputs_from_optimization_context(
        _mock_two_district_om(),
        workdir=workdir,
        model_name="ExchangerTarget",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=_two_polygons(),
        inter_district_pipe_specs={
            (0, 1): {"pipe_capex_base_eur": 10.0},
            (1, 0): {"pipe_capex_base_eur": 10.0},
        },
        data_dir=REPO_ROOT / "data",
        start_year=2020,
        end_year=2025,
        year_gap=5,
        discount_rate=0.05,
        lockout_years=0,
        dt_hours=1,
        elec_price_eur_per_mwh=120.0,
        export_price_eur_per_mwh=0.0,
        grid_prices={"electricity": 120.0, "export": 0.0},
        supply_prices={"gas": 60.0},
        selected_techs=_selected_techs_two_district(),
        retain_existing_output_schedule=[1.0, 1.0],
    )

    xlsx_path = workdir / "Data" / "Techmap" / "ExchangerTarget.xlsx"
    assert xlsx_path.exists()

    df = pd.read_excel(xlsx_path, sheet_name="ConversionSubProcess")
    assert not df.empty

    pipe_rows = df[df["conversion_process_name"].astype(str).str.startswith("Pipe_D")]
    assert not pipe_rows.empty
    assert pipe_rows["min_eout"].isna().all()

    hx_row_d0 = df[df["conversion_process_name"] == "HeatExchanger_D0"].iloc[0]
    hx_row_d1 = df[df["conversion_process_name"] == "HeatExchanger_D1"].iloc[0]
    assert _profile_peak(hx_row_d0.get("min_eout")) is None
    assert _profile_peak(hx_row_d1.get("min_eout")) is None

    # D0 has historical Fernwaerme in mock data and remains available.
    assert (_profile_peak(hx_row_d0.get("cap_max")) or 0.0) > 0.0

    # D1 has central tech data but no historical Fernwaerme output.
    # DHN assets must start at zero retained state, but remain buildable by CESM.
    assert (_profile_peak(hx_row_d1.get("cap_max")) or 0.0) > 0.0
    hg_row_d1 = df[df["conversion_process_name"] == "heat_grid_D1"].iloc[0]
    assert (_profile_peak(hg_row_d1.get("cap_max")) or 0.0) > 0.0
    hx_residual = _profile_peak(hx_row_d1.get("cap_res_min"))
    hg_residual = _profile_peak(hg_row_d1.get("cap_res_min"))
    assert hx_residual is None or hx_residual == pytest.approx(0.0)
    assert hg_residual is None or hg_residual == pytest.approx(0.0)

    # Central injections in no-Fernwaerme districts must not force retained
    # heat output, otherwise they can backdoor DHN build in lockout years.
    cen_d0 = df[df["conversion_process_name"] == "cen_heat_pump_D0"].iloc[0]
    cen_d1 = df[df["conversion_process_name"] == "cen_heat_pump_D1"].iloc[0]
    assert _profile_peak(cen_d0.get("min_eout")) is not None
    assert _profile_peak(cen_d1.get("min_eout")) is None


def fernwaerme_dependency_default_on_t(tmp_path):
    """Checks non-historical districts keep zero DHN residuals but stay buildable."""
    workdir = tmp_path / "cesm_fernwaerme_default_on"
    _ensure_tss(workdir)

    _write_cesm_inputs_from_optimization_context(
        _mock_two_district_om(),
        workdir=workdir,
        model_name="FernwaermeDefaultOn",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=_two_polygons(),
        inter_district_pipe_specs={
            (0, 1): {"pipe_capex_base_eur": 10.0},
            (1, 0): {"pipe_capex_base_eur": 10.0},
        },
        data_dir=REPO_ROOT / "data",
        start_year=2020,
        end_year=2025,
        year_gap=5,
        discount_rate=0.05,
        lockout_years=0,
        dt_hours=1,
        elec_price_eur_per_mwh=120.0,
        export_price_eur_per_mwh=0.0,
        grid_prices={"electricity": 120.0, "export": 0.0},
        supply_prices={"gas": 60.0},
        selected_techs=_selected_techs_two_district(),
        retain_existing_output_schedule=[1.0, 1.0],
    )

    xlsx_path = workdir / "Data" / "Techmap" / "FernwaermeDefaultOn.xlsx"
    assert xlsx_path.exists()

    df = pd.read_excel(xlsx_path, sheet_name="ConversionSubProcess")
    assert not df.empty

    # D1 has no historical Fernwaerme and therefore no retained DHN capacity,
    # while investment options remain available.
    hx_row_d1 = df[df["conversion_process_name"] == "HeatExchanger_D1"].iloc[0]
    assert (_profile_peak(hx_row_d1.get("cap_max")) or 0.0) > 0.0
    hx_residual = _profile_peak(hx_row_d1.get("cap_res_min"))
    assert hx_residual is None or hx_residual == pytest.approx(0.0)

    hg_row_d1 = df[df["conversion_process_name"] == "heat_grid_D1"].iloc[0]
    assert (_profile_peak(hg_row_d1.get("cap_max")) or 0.0) > 0.0
    hg_residual = _profile_peak(hg_row_d1.get("cap_res_min"))
    assert hg_residual is None or hg_residual == pytest.approx(0.0)


def free_pipe_rows_removed_t(tmp_path):
    """Checks free links use explicit zero-cost Pipe rows with local DHN path semantics."""
    workdir = tmp_path / "cesm_free_pipe_rows_removed"
    _ensure_tss(workdir)

    _write_cesm_inputs_from_optimization_context(
        _mock_two_district_om(),
        workdir=workdir,
        model_name="FreePipeRowsRemoved",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=_two_polygons(),
        inter_district_pipe_specs={
            (0, 1): {"pipe_capex_base_eur": 10.0, "is_free": True},
            (1, 0): {"pipe_capex_base_eur": 10.0, "is_free": True},
        },
        data_dir=REPO_ROOT / "data",
        start_year=2020,
        end_year=2025,
        year_gap=5,
        discount_rate=0.05,
        lockout_years=0,
        dt_hours=1,
        elec_price_eur_per_mwh=120.0,
        export_price_eur_per_mwh=0.0,
        grid_prices={"electricity": 120.0, "export": 0.0},
        supply_prices={"gas": 60.0},
        selected_techs=_selected_techs_two_district(),
        retain_existing_output_schedule=[1.0, 1.0],
    )

    xlsx_path = workdir / "Data" / "Techmap" / "FreePipeRowsRemoved.xlsx"
    assert xlsx_path.exists()

    df = pd.read_excel(xlsx_path, sheet_name="ConversionSubProcess")
    assert not df.empty

    pipe_rows = df[df["conversion_process_name"].astype(str).str.startswith("Pipe_D")]
    assert not pipe_rows.empty

    pipe_d1_d0 = df[df["conversion_process_name"] == "Pipe_D1_D0"].iloc[0]
    pipe_d0_d1 = df[df["conversion_process_name"] == "Pipe_D0_D1"].iloc[0]
    assert str(pipe_d1_d0.get("commodity_in")) == "district_heat_out_D0"
    assert str(pipe_d1_d0.get("commodity_out")) == "district_heat_in_D1"
    assert float(pipe_d1_d0.get("capex_cost_power") or 0.0) == pytest.approx(0.0)
    assert float(pipe_d1_d0.get("opex_cost_energy") or 0.0) == pytest.approx(0.0)
    pipe_cap_peak = _profile_peak(pipe_d1_d0.get("cap_max")) or 0.0
    assert pipe_cap_peak > 0.0
    assert (_profile_peak(pipe_d1_d0.get("cap_res_min")) or 0.0) == pytest.approx(pipe_cap_peak)
    assert (_profile_peak(pipe_d1_d0.get("cap_res_max")) or 0.0) == pytest.approx(pipe_cap_peak)
    assert _profile_peak(pipe_d1_d0.get("capex_cost_base")) is None

    assert str(pipe_d0_d1.get("commodity_in")) == "district_heat_out_D1"
    assert str(pipe_d0_d1.get("commodity_out")) == "district_heat_in_D0"
    assert float(pipe_d0_d1.get("capex_cost_power") or 0.0) == pytest.approx(0.0)
    assert float(pipe_d0_d1.get("opex_cost_energy") or 0.0) == pytest.approx(0.0)
    pipe_cap_peak_rev = _profile_peak(pipe_d0_d1.get("cap_max")) or 0.0
    assert pipe_cap_peak_rev > 0.0
    assert (_profile_peak(pipe_d0_d1.get("cap_res_min")) or 0.0) == pytest.approx(pipe_cap_peak_rev)
    assert (_profile_peak(pipe_d0_d1.get("cap_res_max")) or 0.0) == pytest.approx(pipe_cap_peak_rev)
    assert _profile_peak(pipe_d0_d1.get("capex_cost_base")) is None

    cen_d0 = df[df["conversion_process_name"] == "cen_heat_pump_D0"].iloc[0]
    cen_d1 = df[df["conversion_process_name"] == "cen_heat_pump_D1"].iloc[0]
    assert str(cen_d0.get("commodity_out")) == "district_heat_in_D0"
    assert str(cen_d1.get("commodity_out")) == "district_heat_in_D1"

    hg_d0 = df[df["conversion_process_name"] == "heat_grid_D0"].iloc[0]
    hg_d1 = df[df["conversion_process_name"] == "heat_grid_D1"].iloc[0]
    assert str(hg_d0.get("commodity_in")) == str(cen_d0.get("commodity_out"))
    assert str(hg_d1.get("commodity_in")) == str(cen_d1.get("commodity_out"))
    assert str(hg_d0.get("commodity_out")) == "district_heat_out_D0"
    assert str(hg_d1.get("commodity_out")) == "district_heat_out_D1"

    hx_d0 = df[df["conversion_process_name"] == "HeatExchanger_D0"].iloc[0]
    hx_d1 = df[df["conversion_process_name"] == "HeatExchanger_D1"].iloc[0]
    assert str(hx_d0.get("commodity_in")) == str(hg_d0.get("commodity_out"))
    assert str(hx_d1.get("commodity_in")) == str(hg_d1.get("commodity_out"))
