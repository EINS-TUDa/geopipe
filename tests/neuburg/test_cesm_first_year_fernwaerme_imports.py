from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
import geopandas as gpd
import pytest

from cesm.core.input_parser import Parser
from cesm.core.model import Model
from pypeline.energy_system.dhn import (
    build_district_heat_grid_from_polygons,
    build_inter_dhn_pipes_from_street_segments,
)
from pypeline.energy_technology.technology import Technology
from pypeline.energy_technology.technology_registry import TechnologyRegistry, get_default_technology_registry
from pypeline.optimization.optimization_context import OptimizationContext
from pypeline.energy_system.heating_shares import resolve_fernwaerme_share_by_district
from pypeline.optimization.cesm.input_writer import _write_cesm_inputs_from_optimization_context

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cesm_in_memory(*, workdir: Path, model_name: str, scenario_name: str = "Base") -> sqlite3.Connection:
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir = workdir / "Data" / "TimeSeries"
    conn = sqlite3.connect(":memory:")
    parser = Parser(model_name, techmap_dir_path=techmap_dir, ts_dir_path=ts_dir, db_conn=conn, scenario=scenario_name)
    parser.parse()
    model = Model(conn=conn)
    model.solve()
    assert model.model.SolCount > 0, f"CESM solve produced no feasible solution (status={model.model.Status})"
    model.save_output()
    return conn


def _build_multidistrict_om(*, region_ids: list[int], annual_mwh: float, import_share_by_region: dict[int, float]) -> OptimizationContext:
    years = [2020, 2025, 2030]
    annual_demand = {rid: {year: float(annual_mwh) for year in years} for rid in region_ids}
    demand_profile = [1.0 / 8760.0] * 8760

    region_metrics: dict[int, dict[str, dict[str, float]]] = {}
    for rid in region_ids:
        region_metrics[rid] = {
            f"heat_grid_D{rid}": {"initial_capacity": 4000.0, "initial_energy_output": float(annual_mwh)},
            f"heat_exchanger_D{rid}": {"initial_capacity": 4000.0, "initial_energy_output": float(annual_mwh)},
        }
    source = region_ids[0]
    region_metrics[source][f"cen_heat_pump_D{source}"] = {
        "initial_capacity": 12000.0,
        "initial_energy_output": 2.0 * float(annual_mwh),
    }

    om = OptimizationContext(
        years=years,
        regions=region_ids,
        commodity="residential_heat",
        annual_demand=annual_demand,
        demand_profile=demand_profile,
        schedules={},
        tss_indices=[],
        tss_weights=[],
        constraints={"min_pipe_import_share_by_region": import_share_by_region},
        technologies={},
        region_technology_metrics=region_metrics,
    )
    om.scenario = SimpleNamespace(lockout_years=1)
    return om


def _selected_techs_for_regions(region_ids: list[int]) -> list[Technology]:
    techs = [
        Technology(
            "grid_electricity",
            "Dummy",
            "Electricity",
            efficiency=1.0,
            technical_lifetime=40,
            opex_cost_energy=80.0,
            opex_cost_power=0.0,
            capex_cost_power=0.0,
            capex_cost_base=0.0,
            cap_max=1e6,
            max_units=1,
        )
    ]
    for rid in region_ids:
        techs.append(
            Technology(
                f"heat_grid_D{rid}",
                f"district_heat_in_D{rid}",
                f"district_heat_out_D{rid}",
                cap_max=5000.0,
                max_units=10,
            )
        )
        techs.append(
            Technology(
                f"heat_exchanger_D{rid}",
                f"district_heat_out_D{rid}",
                f"residential_heat_D{rid}",
                cap_max=5000.0,
                max_units=10,
            )
        )
    source = region_ids[0]
    techs.append(
        Technology(
            f"cen_heat_pump_D{source}",
            "Electricity",
            f"district_heat_in_D{source}",
            efficiency=3.0,
            technical_lifetime=25,
            opex_cost_energy=0.0,
            opex_cost_power=0.0,
            capex_cost_power=10.0,
            capex_cost_base=0.0,
            cap_max=12000.0,
            max_units=10,
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
            efficiency=0.98,
            technical_lifetime=40,
            opex_cost_energy=0.0,
            opex_cost_power=0.0,
            capex_cost_power=1.0,
            capex_cost_base=0.0,
            cap_max=5000.0,
            max_units=10,
        )
    )
    return registry


def first_year_imports_t(tmp_path: Path) -> None:
    """Checks first-year locked scenario enforces required district heat import level from inter-district pipes."""
    polygon_path = REPO_ROOT / "examples" / "neuburg" / "output_data" / "polygon_neuburg.geojson"
    street_segments_path = REPO_ROOT / "examples" / "neuburg" / "output_data" / "street_segments_neuburg.geojson"
    heating_shares_path = REPO_ROOT / "examples" / "neuburg" / "input_data" / "heating_shares_neuburg.geojson"
    if not polygon_path.exists() or not street_segments_path.exists() or not heating_shares_path.exists():
        pytest.skip("Neuburg fixtures not available")

    polygons = gpd.read_file(polygon_path)
    street_segments = gpd.read_file(street_segments_path)
    heating_shares = gpd.read_file(heating_shares_path)

    if polygons.empty or street_segments.empty or heating_shares.empty:
        pytest.skip("Neuburg fixtures are empty")

    polygons = polygons.copy().reset_index(drop=True)
    polygons["id"] = list(range(len(polygons)))

    fern_share_by_region = resolve_fernwaerme_share_by_district(polygons, heating_shares)
    assert fern_share_by_region, "No positive Fernwärme shares detected in Neuburg heating-share data"

    inter_district_pipe_specs = build_inter_dhn_pipes_from_street_segments(
        polygons=polygons,
        street_segments_gdf=street_segments,
        pipe_capex_eur_per_km=1.0,
        region_id_column="id",
    )
    default_registry = get_default_technology_registry()
    default_registry.load_from_default()
    pipe_tech = default_registry.get_by_name("heat_pipe")
    local_dhn_costs = build_district_heat_grid_from_polygons(
        polygons=polygons,
        local_pipe_capex_eur_per_km=float(pipe_tech.pipe_capex_eur_per_km),
        street_segments_gdf=street_segments,
        region_id_column="id",
    )
    incoming_counts: dict[int, int] = {}
    for (dst, _src) in inter_district_pipe_specs.keys():
        incoming_counts[int(dst)] = incoming_counts.get(int(dst), 0) + 1

    constrained_candidates = [
        rid for rid, share in sorted(fern_share_by_region.items(), key=lambda item: item[1], reverse=True)
        if share > 0.0 and incoming_counts.get(int(rid), 0) > 0
    ]
    assert constrained_candidates, "No Fernwärme district with incoming inter-district pipe found"
    constrained_region = int(constrained_candidates[0])

    import_share_constraint = {constrained_region: float(fern_share_by_region[constrained_region])}
    region_ids = [int(rid) for rid in polygons["id"].tolist()]
    om = _build_multidistrict_om(
        region_ids=region_ids,
        annual_mwh=1000.0,
        import_share_by_region=import_share_constraint,
    )

    workdir = tmp_path / "cesm_first_year_imports_neuburg_dataset"
    tss_source = REPO_ROOT / "CESM" / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    tss_target = workdir / "Data" / "TimeSeries" / "4ThinWeeks.txt"
    tss_target.parent.mkdir(parents=True, exist_ok=True)
    tss_target.write_text(tss_source.read_text(encoding="utf-8"), encoding="utf-8")

    _write_cesm_inputs_from_optimization_context(
        om,
        workdir=workdir,
        model_name="FirstYearFernwaermeImportFromDataset",
        scenario_name="Base",
        tss_name="4ThinWeeks",
        polygons_gdf=polygons,
        inter_district_pipe_specs=inter_district_pipe_specs,
        local_dhn_costs=local_dhn_costs,
        data_dir=REPO_ROOT / "data",
        start_year=2020,
        end_year=2030,
        year_gap=5,
        discount_rate=0.05,
        lockout_years=1,
        dt_hours=1,
        elec_price_eur_per_mwh=80.0,
        export_price_eur_per_mwh=0.0,
        grid_prices={"electricity": 80.0, "export": 0.0},
        supply_prices={"oil": 200.0},
        selected_techs=_selected_techs_for_regions(region_ids),
        technology_registry=_pipe_registry(),
        retain_existing_output_schedule=[1.0, 1.0, 1.0],
    )

    conn = _run_cesm_in_memory(
        workdir=workdir,
        model_name="FirstYearFernwaermeImportFromDataset",
        scenario_name="Base",
    )
    cur = conn.cursor()

    cur.execute(
        """
        SELECT cp.name, SUM(o.eouttot)
        FROM output_cs_y o
        JOIN conversion_subprocess cs ON cs.id = o.cs_id
        JOIN conversion_process cp ON cp.id = cs.cp_id
        JOIN year y ON y.id = o.y_id
        WHERE y.value = 2020
          AND cp.name LIKE 'Pipe_D%'
        GROUP BY cp.name
        """
    )
    pipe_rows = cur.fetchall()
    assert pipe_rows, "Expected first-year pipe dispatch rows"

    imports_by_region: dict[int, float] = {}
    for cp_name, eouttot in pipe_rows:
        match = re.match(r"^Pipe_D(\d+)_D(\d+)$", str(cp_name))
        if match is None:
            continue
        dst = int(match.group(1))
        imports_by_region[dst] = imports_by_region.get(dst, 0.0) + float(eouttot or 0.0)

    expected_fernwaerme_demand = float(om.annual_demand[constrained_region][2020]) * float(
        import_share_constraint[constrained_region]
    )
    actual_import = float(imports_by_region.get(constrained_region, 0.0))
    assert actual_import + 1e-6 >= expected_fernwaerme_demand, (
        f"Region D{constrained_region} imports in 2020 ({actual_import:.6f} MWh) "
        f"must cover dataset-derived Fernwärme demand ({expected_fernwaerme_demand:.6f} MWh)"
    )
