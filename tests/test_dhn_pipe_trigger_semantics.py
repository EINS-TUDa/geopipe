from pathlib import Path
import re
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Polygon
from pypeline.energy_technology.technology import Technology
from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.optimization.om_adapter import OMContext
from tools.cesm_plugin import _write_cesm_inputs_from_om

REPO_ROOT = Path(__file__).resolve().parents[1]


DISTRICT_A = 0
DISTRICT_B = 1


def _dn(name: str, district: int) -> str:
    return f"{name}_D{district}"


def _heat_in(district: int) -> str:
    return _dn("district_heat_in", district)


def _heat_out(district: int) -> str:
    return _dn("district_heat_out", district)


def _heat_demand(district: int) -> str:
    return _dn("residential_heat", district)


def _build_om(region_ids: list[int], region_metrics: dict | None = None) -> OMContext:
    years = [2020, 2025, 2030]
    regions = list(region_ids)
    annual_demand = {rid: {year: 1000.0 for year in years} for rid in regions}
    demand_profile = [1.0 / 8760.0] * 8760
    return OMContext(
        years=years,
        regions=regions,
        commodity="residential_heat",
        annual_demand=annual_demand,
        demand_profile=demand_profile,
        schedules={},
        tss_indices=[],
        tss_weights=[],
        constraints={},
        technologies={},
        region_technology_metrics=region_metrics or {},
    )




def _toy_pipe_registry_for_build_trigger() -> TechnologyRegistry:
    registry = TechnologyRegistry()
    registry.register(
        Technology(
            "heat_pipe",
            "district_heat_out",
            "district_heat_in",
            technical_lifetime=40,
            opex_cost_energy=2.0,
            capex_cost_power=10.0,
            capex_cost_base=0.0,
            cap_max=500.0,
            max_units=100,
        )
    )
    return registry


def _ensure_tss(workdir: Path) -> None:
    tss_source = REPO_ROOT / "CESM" / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    assert tss_source.exists(), "Reference TSS file missing: CESM/Data/TimeSeries/4ThinWeeks.txt"
    tss_target = workdir / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    tss_target.parent.mkdir(parents=True, exist_ok=True)
    tss_target.write_text(tss_source.read_text(encoding="utf-8"), encoding="utf-8")




def _toy_two_district_polygons() -> gpd.GeoDataFrame:
    rows = [
        {
            "id": DISTRICT_A,
            "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "street_length_m": 1200.0,
            "demand_street_length_m": 1000.0,
            "nondemand_street_length_m": 200.0,
        },
        {
            "id": DISTRICT_B,
            "geometry": Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            "street_length_m": 2400.0,
            "demand_street_length_m": 2000.0,
            "nondemand_street_length_m": 400.0,
        },
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3035")


def _toy_two_district_street_segments() -> gpd.GeoDataFrame:
    rows = [
        {
            "id": DISTRICT_A,
            "_is_demand_street": True,
            "geometry": LineString([(0.0, 0.0), (1000.0, 0.0)]),
        },
        {
            "id": None,
            "_is_demand_street": False,
            "geometry": LineString([(1000.0, 0.0), (1300.0, 0.0)]),
        },
        {
            "id": DISTRICT_B,
            "_is_demand_street": True,
            "geometry": LineString([(1300.0, 0.0), (3300.0, 0.0)]),
        },
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:3035")


def _write_inputs_with_techs(
    *,
    workdir: Path,
    model_name: str,
    polygons: gpd.GeoDataFrame,
    om: OMContext,
    techs: list[Technology],
    segments: gpd.GeoDataFrame | None = None,
) -> pd.DataFrame:
    _ensure_tss(workdir)
    _write_cesm_inputs_from_om(
        om,
        workdir=workdir,
        model_name=model_name,
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=polygons,
        street_segments_gdf=segments,
        data_dir=REPO_ROOT / "data",
        start_year=2020,
        end_year=2030,
        year_gap=5,
        discount_rate=0.05,
        lockout_years=0,
        dt_hours=1,
        heat_commodity_base="residential_heat",
        elec_price_eur_per_mwh=120.0,
        export_price_eur_per_mwh=0.0,
        grid_prices={"electricity": 120.0, "export": 0.0},
        supply_prices={"oil": 80.0},
        selected_techs=techs,
        technology_registry=_toy_pipe_registry_for_build_trigger(),
        retain_existing_output_schedule=[1.0, 1.0, 1.0],
    )
    xlsx = workdir / "Data" / "Techmap" / f"{model_name}.xlsx"
    assert xlsx.exists()
    return pd.read_excel(xlsx, sheet_name="ConversionSubProcess")


def _capex_base_for_cp(convsub_df: pd.DataFrame, cp_name: str) -> float:
    rows = convsub_df[convsub_df["conversion_process_name"] == cp_name]
    assert not rows.empty, f"Missing conversion process row for {cp_name}"
    return float(rows.iloc[0]["capex_cost_base"])


def _pipe_rows(convsub_df: pd.DataFrame) -> pd.DataFrame:
    return convsub_df[convsub_df["conversion_process_name"].astype(str).str.startswith("Pipe_D", na=False)].copy()


def _peak_profile(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    txt = str(value).strip()
    if not txt:
        return 0.0
    if txt.startswith("[") and txt.endswith("]"):
        peak = 0.0
        for chunk in txt[1:-1].split(";"):
            parts = chunk.strip().split()
            if len(parts) < 2:
                continue
            peak = max(peak, float(parts[1]))
        return peak
    return float(txt)


def _two_district_techs(*, include_central_a: bool, include_exchangers: bool) -> list[Technology]:
    techs = [
        Technology(_dn("heat_grid", DISTRICT_A), _heat_in(DISTRICT_A), _heat_out(DISTRICT_A), capex_cost_base=0.0, cap_max=1000.0, max_units=100),
        Technology(_dn("heat_grid", DISTRICT_B), _heat_in(DISTRICT_B), _heat_out(DISTRICT_B), capex_cost_base=0.0, cap_max=1000.0, max_units=100),
    ]
    if include_central_a:
        techs.append(
            Technology(_dn("cen_heat_pump", DISTRICT_A), "electricity", _heat_in(DISTRICT_A), cap_max=1000.0, max_units=100)
        )
    if include_exchangers:
        techs.extend(
            [
                Technology(_dn("heat_exchanger", DISTRICT_A), _heat_out(DISTRICT_A), _heat_demand(DISTRICT_A), cap_max=1000.0, max_units=100),
                Technology(_dn("heat_exchanger", DISTRICT_B), _heat_out(DISTRICT_B), _heat_demand(DISTRICT_B), cap_max=1000.0, max_units=100),
            ]
        )
    return techs




def central_no_trigger_t(tmp_path: Path) -> None:
    """Checks central build alone does not trigger DHN grid fixed cost without DHN history."""
    polygons = _toy_two_district_polygons()
    om = _build_om([DISTRICT_A, DISTRICT_B])
    techs = _two_district_techs(include_central_a=True, include_exchangers=False)

    convsub_df = _write_inputs_with_techs(
        workdir=tmp_path / "first-central",
        model_name="DHNFirstCentral",
        polygons=polygons,
        om=om,
        techs=techs,
    )

    assert _capex_base_for_cp(convsub_df, _dn("heat_grid", DISTRICT_A)) == 0.0
    assert _capex_base_for_cp(convsub_df, _dn("heat_grid", DISTRICT_B)) == 0.0


def dhn_exists_no_cost_t(tmp_path: Path) -> None:
    """Checks existing DHN prevents additional first-build trigger cost application."""
    polygons = _toy_two_district_polygons()
    metrics = {
        DISTRICT_A: {
            _dn("heat_grid", DISTRICT_A): {
                "initial_capacity": 1.0,
                "initial_energy_output": 0.0,
            }
        }
    }
    om = _build_om([DISTRICT_A, DISTRICT_B], region_metrics=metrics)
    techs = _two_district_techs(include_central_a=True, include_exchangers=False)

    convsub_df = _write_inputs_with_techs(
        workdir=tmp_path / "existing-dhn",
        model_name="DHNExisting",
        polygons=polygons,
        om=om,
        techs=techs,
    )

    assert _capex_base_for_cp(convsub_df, _dn("heat_grid", DISTRICT_A)) == 0.0
    assert _capex_base_for_cp(convsub_df, _dn("heat_grid", DISTRICT_B)) == 0.0


def pipes_connected_t(tmp_path: Path) -> None:
    """Checks connected districts create pipe rows while grid trigger costs stay unchanged."""
    polygons = _toy_two_district_polygons()
    segments = _toy_two_district_street_segments()
    om = _build_om([DISTRICT_A, DISTRICT_B])
    techs = _two_district_techs(include_central_a=False, include_exchangers=True)

    convsub_df = _write_inputs_with_techs(
        workdir=tmp_path / "connected",
        model_name="DHNPipeTrigger",
        polygons=polygons,
        om=om,
        techs=techs,
        segments=segments,
    )

    assert _capex_base_for_cp(convsub_df, _dn("heat_grid", DISTRICT_A)) == 0.0
    assert _capex_base_for_cp(convsub_df, _dn("heat_grid", DISTRICT_B)) == 0.0
    pipe_rows = _pipe_rows(convsub_df)
    assert len(pipe_rows) == 2
    assert set(pipe_rows["conversion_process_name"].astype(str)) == {
        f"Pipe_D{DISTRICT_A}_D{DISTRICT_B}",
        f"Pipe_D{DISTRICT_B}_D{DISTRICT_A}",
    }
    assert (pipe_rows["capex_cost_base"].astype(float) > 0.0).all()


def pipe_metric_floor_t(tmp_path: Path) -> None:
    """Checks existing built pipe metrics trigger DHN reserve floors only for the destination district."""
    polygons = _toy_two_district_polygons()
    segments = _toy_two_district_street_segments()

    baseline_df = _write_inputs_with_techs(
        workdir=tmp_path / "baseline",
        model_name="PipeTriggerBaseline",
        polygons=polygons,
        om=_build_om([DISTRICT_A, DISTRICT_B], region_metrics={}),
        techs=_two_district_techs(include_central_a=False, include_exchangers=True),
        segments=segments,
    )

    pipe_names = [
        str(name)
        for name in baseline_df["conversion_process_name"].dropna().tolist()
        if str(name).startswith("Pipe_D")
    ]
    assert pipe_names, "Expected inter-district pipes in toy topology"

    first_pipe = pipe_names[0]
    match = re.match(r"^Pipe_D(\d+)_D(\d+)$", first_pipe)
    assert match is not None
    dst = int(match.group(1))
    src = int(match.group(2))

    baseline_hx_dst = baseline_df[baseline_df["conversion_process_name"] == _dn("HeatExchanger", dst)].iloc[0]
    baseline_grid_dst = baseline_df[baseline_df["conversion_process_name"] == _dn("heat_grid", dst)].iloc[0]
    assert _peak_profile(baseline_hx_dst.get("cap_res_min")) == 0.0
    assert _peak_profile(baseline_grid_dst.get("cap_res_min")) == 0.0

    region_metrics_with_pipe = {
        DISTRICT_A: {
            f"heat_pipe_D{dst}_D{src}": {
                "initial_capacity": 1.0,
                "initial_energy_output": 0.0,
            }
        }
    }

    triggered_df = _write_inputs_with_techs(
        workdir=tmp_path / "triggered",
        model_name="PipeTriggerWithExistingBuild",
        polygons=polygons,
        om=_build_om([DISTRICT_A, DISTRICT_B], region_metrics=region_metrics_with_pipe),
        techs=_two_district_techs(include_central_a=False, include_exchangers=True),
        segments=segments,
    )

    hx_row = triggered_df[triggered_df["conversion_process_name"] == _dn("HeatExchanger", dst)].iloc[0]
    grid_row = triggered_df[triggered_df["conversion_process_name"] == _dn("heat_grid", dst)].iloc[0]
    hx_src_row = triggered_df[triggered_df["conversion_process_name"] == _dn("HeatExchanger", src)].iloc[0]
    grid_src_row = triggered_df[triggered_df["conversion_process_name"] == _dn("heat_grid", src)].iloc[0]

    assert _peak_profile(hx_row.get("cap_res_min")) > 0.0
    assert _peak_profile(grid_row.get("cap_res_min")) > 0.0
    assert _peak_profile(hx_src_row.get("cap_res_min")) == 0.0
    assert _peak_profile(grid_src_row.get("cap_res_min")) == 0.0
