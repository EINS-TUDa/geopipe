# -*- coding: utf-8 -*-
from pathlib import Path
import sys
import geopandas as gpd
import sqlite3

from pypeline import EnergySystemBuilder, TechnologyRegistry
from pypeline.data.default_registry import get_default_data_registry
from pypeline.energy_system.catalog import register_default_technologies
from pypeline.energy_system.rule_book import EnergySystemRuleBook, MinimumDHNThroughputRule
from pypeline.energy_system.scenario import Scenario
from pypeline.optimization.om_adapter import build_om_from_es
from tools.cesm_plugin import CESMBackend  # unified backend import (replaces pypeline.optimization.cesm_backend)
from pypeline.plot.plotter import EnergySystemPlotter
from tools.cesm_plugin import write_cesm_inputs_from_data  # unified API

def must_exist(p: Path, what: str) -> None:
    if not p.exists():
        raise FileNotFoundError(f"Missing {what}: {p.resolve()}")

def main():
    tech_reg = TechnologyRegistry(); register_default_technologies(tech_reg)
    data_reg = get_default_data_registry()

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
    es_plotter = EnergySystemPlotter(es)
    es_plotter.plot()
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

    # EB tiers
    eb_small_eta=0.95,
    eb_small_capex_eur_per_mw=11000.0,
    eb_small_opex_eur_per_mw=0.0,
    eb_small_opex_eur_per_mwh=0.0,
    eb_small_cap_max_mw=0.1,

    eb_large_eta=0.96,
    eb_large_capex_eur_per_mw=10000.0,
    eb_large_opex_eur_per_mw=0.0,
    eb_large_opex_eur_per_mwh=0.0,
    eb_large_out_frac_min=0.5,
    eb_large_cap_min_mw=0.04,
    eb_large_cap_max_mw=0.85,

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
    # Use unified plugin directly as runner
    runner = project_root / "tools" / "cesm_plugin.py"
    must_exist(runner, "plugin script")

    backend = CESMBackend(
        workdir=str(workdir),
        cli=[sys.executable, str(runner)],
        run_args=["--workdir", ".", "-m", model_name, "-s", scenario_name],
        run_subdir=run_name,
        write_inputs=False,
    )

    solution = backend.optimize(om)

    # Try normal cesm module first (if installed / shimmed), else fall back to local CESM/core paths.
    try:
        from cesm import Plotter, PlotType  # type: ignore
        from cesm import DAO  # type: ignore
    except ImportError:
        cesm_root = Path("CESM").resolve()
        core_dir = cesm_root / "core"
        for p in (cesm_root, core_dir):
            sp = str(p)
            if sp not in sys.path:
                sys.path.insert(0, sp)
        from core.plotter import Plotter, PlotType  # type: ignore
        from core.data_access import DAO  # type: ignore

    db_path = Path("CESM") / "Runs" / run_name / "db.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        dao = DAO(conn)
        plotter = Plotter(dao)

        sankey_fig = plotter.plot_sankey(year=2020)
        sankey_fig.show()

    finally:
        conn.close()

if __name__ == "__main__":
    main()
