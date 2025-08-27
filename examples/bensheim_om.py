# -*- coding: utf-8 -*-
from pathlib import Path
import sys
import geopandas as gpd

from pypeline import DataRegistry, EnergySystemBuilder, TechnologyRegistry
from pypeline.energy_system.rule_book import EnergySystemRuleBook, MinimumDHNThroughputRule
from pypeline.energy_system.scenario import Scenario
from pypeline.optimization.cesm_to_om_adapter import build_om_from_es
from pypeline.optimization.cesm_backend import CESMBackend
from tools.cesm_writer import write_cesm_inputs_from_data 

def must_exist(p: Path, what: str) -> None:
    if not p.exists():
        raise FileNotFoundError(f"Missing {what}: {p.resolve()}")

def main():
    tech_reg = TechnologyRegistry(); tech_reg.load_from_default()
    data_reg = DataRegistry();       data_reg.load_from_default()
    polygons = gpd.read_file(Path("data") / "wah_bensheim_4_districts.geojson")

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

    write_cesm_inputs_from_data(
        om,
        workdir=workdir,
        model_name=model_name,
        scenario_name=scenario_name,
        tss_name=tss_name,
        data_dir=Path("data"),
        heat_file="D_Heat_Household_J.txt",
        elec_profile_file="corrected_eletricity_demand_2016.txt",
        electric_boiler_eta=0.95,
        elec_price_eur_per_mwh=120.0,  
        export_price_eur_per_mwh=0.0,  
        cap_max_mw=1e6,
        start_year=2020, end_year=2030, year_gap=5,
        discount_rate=0.05, dt_hours=1,
    )

    xlsx = techmap_dir / f"{model_name}.xlsx"
    tss  = ts_dir / f"{tss_name}.txt"
    must_exist(xlsx, "Techmap workbook")
    must_exist(tss, "Time-series file")

    print(f"Techmaps in CESM/Data/Techmap: {[p.stem for p in techmap_dir.glob('*.xlsx')]}")
    print(f"Expecting CESM to run: -m {model_name} -s {scenario_name}")
    print(f"TSS file present: {tss.resolve()}")

    project_root = Path(__file__).resolve().parents[1]
    runner = project_root / "tools" / "cesm_runner.py"
    must_exist(runner, "runner script")

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

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
        sys.exit(1)
