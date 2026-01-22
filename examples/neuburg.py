# -*- coding: utf-8 -*-
from pathlib import Path
import sys
import geopandas as gpd
import sqlite3
project_root = Path(__file__).resolve().parents[1]

from pypeline import EnergySystemBuilder, TechnologyRegistry
from pypeline.data.default_registry import get_default_data_registry
from pypeline.energy_system.rule_book import EnergySystemRuleBook, MinimumDHNThroughputRule
from pypeline.energy_system.scenario import Scenario
from tools.cesm_plugin import CESMBackend 
from pypeline.plot.plotter import EnergySystemPlotter

def must_exist(p: Path, what: str) -> None:
    if not p.exists():
        raise FileNotFoundError(f"Missing {what}: {p.resolve()}")

def main():
    tech_reg = TechnologyRegistry(); tech_reg.load_from_default()
    data_reg = get_default_data_registry()

    polygons_path = project_root / "data/projects/neuburg/polygon_neuburg.geojson"
    polygons = gpd.read_file(Path(polygons_path))

    esb = EnergySystemBuilder(energy_system_name="neuburg")
    esb.set_polygons(polygons)
    esb.set_technology_dependency_manager(default=True)
    esb.set_demands(default=True)
    esb.set_technology_registry(tech_reg)
    esb.set_data_registry(data_reg)

    rulebook = EnergySystemRuleBook()
    rulebook.add_rule(MinimumDHNThroughputRule(demand_name="residential_heat", min_share=0.20))
    esb.set_energy_system_rule_book(rulebook)

    es = esb.build()
    es.data_dir = project_root / "data"

    es_plotter = EnergySystemPlotter(es)
    es_plotter.plot()
    print("Constraints:", getattr(es, "constraints", {}))

    model_name    = "Neuburg"
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
    )

    workdir     = Path("CESM")
    techmap_dir = workdir / "Data" / "Techmap"
    ts_dir      = workdir / "Data" / "TimeSeries"
    techmap_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)

    runner = project_root / "tools" / "cesm_plugin.py"
    must_exist(runner, "plugin script")

    backend = CESMBackend(
        workdir=str(workdir),
        cli=[sys.executable, str(runner)],
        run_args=["--workdir", ".", "-m", model_name, "-s", scenario_name],
        run_subdir=run_name,
        write_inputs=True,
        scenario=scenario,
        model_name=model_name,
        scenario_name=scenario_name,
        tss_name=tss_name,
    )

    solution = backend.optimize(es, scenario=scenario, demand_name="residential_heat")

    from cesm.core.plotter import Plotter, PlotType
    from cesm.core.data_access import DAO
    db_path = Path("CESM") / "Runs" / run_name / "db.sqlite"
    conn = sqlite3.connect(str(db_path))
    dao = DAO(conn)
    plotter = Plotter(dao)

    sankey_fig = plotter.plot_sankey(year=2020)
    sankey_fig.show()
    sankey_fig2 = plotter.plot_sankey(year=2030)
    sankey_fig2.show()

    # plotter.plot_timeseries(
    #     timeseries_type=PlotType.TimeSeries.POWER_CONSUMPTION,
    #     year=2020,
    #     commodity="Electricity",
    # )

    conn.close()

if __name__ == "__main__":
    main()
