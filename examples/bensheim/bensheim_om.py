# -*- coding: utf-8 -*-
import os
from pathlib import Path
import sys
import geopandas as gpd
import sqlite3
import matplotlib.pyplot as plt
project_root = Path(__file__).resolve().parents[2]
from pypeline import EnergySystemBuilder, TechnologyRegistry
from pypeline.energy_system.rule_book import (
    EnergySystemRuleBook,
    MinimumDHNThroughputRule,
    MinimumCentralCapacityRule,
)
from pypeline.energy_system.scenario import Scenario
from pypeline.plot.plotter import EnergySystemPlotter
from tools.cesm_plugin import CESMBackend
from pypeline.data.default_registry import get_default_data_registry

def must_exist(p: Path, what: str) -> None:
    if not p.exists():
        raise FileNotFoundError(f"Missing {what}: {p.resolve()}")

def main():
    tech_reg = TechnologyRegistry()
    tech_reg.load_from_default()
    bensheim_folder = project_root / "examples" / "bensheim"
    plots_dir = bensheim_folder / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    polygons_path = project_root / "examples" / "bensheim" / "wah_bensheim_4_districts.geojson"
    polygons = gpd.read_file(polygons_path)


    data_reg = get_default_data_registry(
        mode="local",
        local_heat_demand_file=project_root / "data" / "WaermeatlasHessen.gpkg",
        local_heating_shares_file=project_root / "data" / "Census2022HeatingType100mGrid" / "Census2022HeatingType100mGrid_Polygons_southhessen.geojson",
    )

    esb = EnergySystemBuilder(energy_system_name="Bensheim")
    esb.set_polygons(polygons)
    esb.set_technology_dependency_manager(default=True)
    esb.set_demands(default=True)
    esb.set_technology_registry(tech_reg)
    esb.set_data_registry(data_reg)

    esb.set_default_region_builder_config()
    esb.region_builder_config.update({
        "min_heat_grid_share": 0.20,
        "heat_grid_names": ("heat_exchanger",),
    })

    rulebook = EnergySystemRuleBook()
    rulebook.add_rule(MinimumDHNThroughputRule(demand_name="residential_heat", min_share=0.25))
    rulebook.add_rule(MinimumCentralCapacityRule(demand_name="residential_heat", min_share_of_demand=0.50,commodity="gas",))
    esb.set_energy_system_rule_book(rulebook)

    es = esb.build()
    es_plotter = EnergySystemPlotter(es)

    model_name    = "Bensheim"
    scenario_name = "Base4twk"
    tss_name      = "4ThinWeeks"
    run_name      = f"{model_name}-{scenario_name}"
    scenario = Scenario(
        name=run_name,
        start_year=2020,
        end_year=2030,
        year_gap=5,
        tss=tss_name,
        retain_existing_output_drop_per_year=0.05,
        lockout_years=2,
    )
    
    workdir     = Path("CESM")
    techmap_dir = workdir / "Data" / "Techmap"
    es.data_dir = project_root / "data"
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
        results_db_name="db.sqlite",
        write_inputs=True,
        model_name=model_name,
        scenario_name=scenario_name,
        tss_name=tss_name,
        scenario=scenario,
        demand_name="residential_heat",
    )

    solution = backend.optimize(es, scenario=scenario, demand_name="residential_heat")
    visible = sorted([p.stem for p in techmap_dir.glob("*.xlsx") if p.is_file()])
    print(f"Techmaps in CESM/Data/Techmap: {visible}")
    print(solution.results)

    years = list(range(int(scenario.start_year), int(scenario.end_year) + 1, int(scenario.year_gap)))
    mix_plot_paths = es_plotter.save_default_mix_plots(
        solution.results,
        years=years,
        plots_dir=plots_dir,
    )
    technology_plot_path = mix_plot_paths["technology"]
    print(f"Saved technology plot: {technology_plot_path}")
    plt.show()

    from cesm.core.plotter import Plotter, PlotType
    from cesm.core.data_access import DAO
    db_path = Path(solution.results["db"])
    if not db_path.is_absolute():
        db_path = project_root / db_path
    print(f"Using DB: {db_path}")
    conn = sqlite3.connect(str(db_path))
    dao = DAO(conn)
    plotter = Plotter(dao)

    for year in years:
        sankey_fig = plotter.plot_sankey(year=year)
        sankey_fig.show()

    conn.close()


if __name__ == "__main__":
    main()
