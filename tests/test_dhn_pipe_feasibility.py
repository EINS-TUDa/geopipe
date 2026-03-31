from pathlib import Path
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Polygon
from pypeline.energy_technology.technology import Technology
from pypeline.energy_technology.technology_registry import TechnologyRegistry
from pypeline.optimization.optimization_context import OptimizationContext
from pypeline.optimization.cesm.input_writer import _write_cesm_inputs_from_optimization_context

REPO_ROOT = Path(__file__).resolve().parents[1]


def _synthetic_polygons() -> gpd.GeoDataFrame:
    polygons = gpd.GeoDataFrame(
        {
            "id": [0, 1, 2],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
                Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
            ],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    return polygons


def _synthetic_street_segments() -> gpd.GeoDataFrame:
    segments = gpd.GeoDataFrame(
        {
            "id": [0, None, 1, None, 2],
            "_is_demand_street": [True, False, True, False, True],
            "geometry": [
                LineString([(0.2, 0.5), (0.9, 0.5)]),
                LineString([(0.9, 0.5), (1.1, 0.5)]),
                LineString([(1.1, 0.5), (1.9, 0.5)]),
                LineString([(1.9, 0.5), (2.1, 0.5)]),
                LineString([(2.1, 0.5), (2.8, 0.5)]),
            ],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    return segments


def _build_mock_om(region_count: int) -> OptimizationContext:
    years = [2020, 2025, 2030]
    regions = list(range(region_count))
    annual_demand = {rid: {year: 1000.0 for year in years} for rid in regions}
    demand_profile = [1.0 / 8760.0] * 8760
    return OptimizationContext(
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
        region_technology_metrics={},
    )


def _selected_techs(region_count: int) -> list[Technology]:
    techs: list[Technology] = []
    for did in range(region_count):
        techs.append(
            Technology(
                f"heat_grid_D{did}",
                f"district_heat_in_D{did}",
                f"district_heat_out_D{did}",
                cap_max=1000.0,
                max_units=100,
            )
        )
        techs.append(
            Technology(
                f"heat_exchanger_D{did}",
                f"district_heat_out_D{did}",
                f"residential_heat_D{did}",
                cap_max=1000.0,
                max_units=100,
            )
        )
    return techs


def _pipe_registry() -> TechnologyRegistry:
    registry = TechnologyRegistry()
    registry.register(
        Technology(
            "heat_pipe",
            "district_heat_out",
            "district_heat_in",
            technical_lifetime=40,
            opex_cost_energy=2.0,
            capex_cost_power=30.0,
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


def pipes_feasible_t(tmp_path):
    """Checks inter-district pipe rows are generated with feasible nonnegative costs."""
    polygons = _synthetic_polygons()
    street_segments = _synthetic_street_segments()
    om = _build_mock_om(region_count=len(polygons))
    retain_schedule = [1.0, 0.95, 0.90]
    selected_techs = _selected_techs(len(polygons))
    registry = _pipe_registry()

    workdir = tmp_path / "cesm"
    _ensure_tss(workdir)

    _write_cesm_inputs_from_optimization_context(
        om,
        workdir=workdir,
        model_name="SyntheticPipeTest",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=polygons,
        street_segments_gdf=street_segments,
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
        supply_prices={"oil": 80.0},
        selected_techs=selected_techs,
        technology_registry=registry,
        retain_existing_output_schedule=retain_schedule,
    )

    xlsx_path = workdir / "Data" / "Techmap" / "SyntheticPipeTest.xlsx"
    assert xlsx_path.exists()

    convproc_df = pd.read_excel(xlsx_path, sheet_name="ConversionProcess")
    convsub_df = pd.read_excel(xlsx_path, sheet_name="ConversionSubProcess")

    pipe_cp_names = [
        str(name)
        for name in convproc_df["conversion_process_name"].dropna().tolist()
        if str(name).startswith("Pipe_D")
    ]
    assert pipe_cp_names

    pipe_rows = convsub_df[
        convsub_df["conversion_process_name"].astype(str).str.startswith("Pipe_D", na=False)
    ]
    assert not pipe_rows.empty
    capex_values = pipe_rows["capex_cost_base"].astype(float)
    assert (capex_values >= 0.0).all()
    assert (capex_values > 0.0).any()


def no_pipes_no_segs_t(tmp_path):
    """Checks no inter-district pipe rows are created without street segment input."""
    polygons = _synthetic_polygons()

    om = _build_mock_om(region_count=len(polygons))
    retain_schedule = [1.0, 0.95, 0.90]
    selected_techs = _selected_techs(len(polygons))
    registry = _pipe_registry()

    workdir = tmp_path / "cesm"
    _ensure_tss(workdir)

    _write_cesm_inputs_from_optimization_context(
        om,
        workdir=workdir,
        model_name="SyntheticNoPipeTest",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=polygons,
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
        supply_prices={"oil": 80.0},
        selected_techs=selected_techs,
        technology_registry=registry,
        retain_existing_output_schedule=retain_schedule,
    )

    xlsx_path = workdir / "Data" / "Techmap" / "SyntheticNoPipeTest.xlsx"
    assert xlsx_path.exists()

    convproc_df = pd.read_excel(xlsx_path, sheet_name="ConversionProcess")
    convsub_df = pd.read_excel(xlsx_path, sheet_name="ConversionSubProcess")

    pipe_cp_names = [
        str(name)
        for name in convproc_df["conversion_process_name"].dropna().tolist()
        if str(name).startswith("Pipe_D")
    ]
    assert pipe_cp_names == []

    pipe_rows = convsub_df[
        convsub_df["conversion_process_name"].astype(str).str.startswith("Pipe_D", na=False)
    ]
    assert pipe_rows.empty
