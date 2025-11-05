# -*- coding: utf-8 -*-
import os
from pathlib import Path
import sys
import geopandas as gpd
import sqlite3

from pypeline import DataRegistry, EnergySystemBuilder, TechnologyRegistry
from pypeline.energy_system.rule_book import EnergySystemRuleBook, MinimumDHNThroughputRule
from pypeline.energy_system.scenario import Scenario
from pypeline.optimization.om_adapter import build_om_from_es
from tools.cesm_plugin import CESMBackend, write_cesm_inputs_from_data

def must_exist(p: Path, what: str) -> None:
    if not p.exists():
        raise FileNotFoundError(f"Missing {what}: {p.resolve()}")

def main():
    tech_reg = TechnologyRegistry(); tech_reg.load_from_default()
    data_reg = DataRegistry();       data_reg.load_from_default()
    polygons_path = Path("data") / "wah_bensheim_4_districts.geojson"
    polygons = gpd.read_file(Path(polygons_path))

    esb = EnergySystemBuilder(energy_system_name="Bensheim")
    esb.set_polygons(polygons)
    esb.set_technology_dependency_manager(default=True)
    esb.set_demands(default=True)
    esb.set_technology_registry(tech_reg)
    esb.set_data_registry(data_reg)

    rulebook = EnergySystemRuleBook()
    rulebook.add_rule(MinimumDHNThroughputRule(demand_name="residential_heat", min_share=0.20))
    esb.set_energy_system_rule_book(rulebook)

    es = esb.build()
    print("Constraints:", getattr(es, "constraints", {}))

    model_name    = "Bensheim"
    scenario_name = "Base4twk"
    tss_name      = "4ThinWeeks"
    run_name      = f"{model_name}-{scenario_name}"

    scenario = Scenario(name=run_name, start_year=2020, end_year=2030, year_gap=5, tss=tss_name)
    om = build_om_from_es(es, scenario, demand_name="residential_heat")

    workdir     = Path("CESM")
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir      = workdir / "Data" / "TimeSeries"
    techmap_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)
    heat_file = "D_Heat_Household_J.txt"
    electricity_file = "corrected_eletricity_demand_2016.txt"

    write_cesm_inputs_from_data(
    om,
    workdir=workdir,
    model_name=model_name,
    scenario_name=scenario_name,
    tss_name=tss_name,
    polygons_path=polygons_path,
    data_dir=Path("data"),
    heat_file=heat_file,
    heat_unit="MWH",
    elec_profile_file=electricity_file,
    heat_commodity_base="Heat",
    # Pipes
    pipe_loss_fraction=0.01,
    pipe_capex_eur_per_mw=50000,
    pipe_opex_eur_per_mwh=0.0,
    pipe_cap_max_mw=10.0,
    )

    xlsx = techmap_dir / f"{model_name}.xlsx"
    tss  = ts_dir / f"{tss_name}.txt"
    must_exist(xlsx, "Techmap workbook")
    must_exist(tss,  "Time-series file")

    visible = sorted([p.stem for p in techmap_dir.glob("*.xlsx") if p.is_file()])
    print(f"Techmaps in CESM/Data/Techmap: {visible}")
    print(f"Expecting CESM to run: -m {model_name} -s {scenario_name}")
    print(f"TSS file present: {tss.resolve()}")

    project_root = Path(__file__).resolve().parents[1]
    runner = project_root / "tools" / "cesm_plugin.py"
    must_exist(runner, "runner script")

    existing_py_path = os.environ.get("PYTHONPATH")
    if existing_py_path:
        os.environ["PYTHONPATH"] = f"{project_root}{os.pathsep}{existing_py_path}"
    else:
        os.environ["PYTHONPATH"] = str(project_root)

    backend = CESMBackend(
        workdir=str(workdir),
        cli=[sys.executable, str(runner)],
        run_args=["--workdir", ".", "-m", model_name, "-s", scenario_name],
        run_subdir=run_name,
        write_inputs=False,
    )

    solution = backend.optimize(om)
    print("Using DB:", solution.results.get("db"))
    print(solution.results)

    try:
        sys.path.insert(0, str(Path("CESM")))
        from core.plotter import Plotter, PlotType
        from core.data_access import DAO

        db_path = Path("CESM") / "Runs" / run_name / "db.sqlite"
        conn = sqlite3.connect(str(db_path))
        try:
            dao = DAO(conn)
            plotter = Plotter(dao)

            sankey_fig = plotter.plot_sankey(year=2020)
            sankey_fig.show()

            # plotter.plot_timeseries(
            #     timeseries_type=PlotType.TimeSeries.POWER_CONSUMPTION,
            #     year=2020,
            #     commodity="Electricity",
            # )
            # plotter.plot_timeseries(
            #     timeseries_type=PlotType.TimeSeries.POWER_PRODUCTION,
            #     year=2020,
            #     commodity="Electricity",
            # )
        finally:
            conn.close()
    except ModuleNotFoundError:
        print("Plotting skipped - CESM core is not on PYTHONPATH")
    except Exception as e:
        print(f"Plotting failed: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
        sys.exit(1)
