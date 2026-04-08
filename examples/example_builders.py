# Helper functions for use in examples.

from __future__ import annotations
from pathlib import Path
import os
import sys
import sqlite3
from typing import Tuple
import geopandas as gpd
import matplotlib.pyplot as plt
from pypeline.data.default_registry import get_default_data_registry
from pypeline.optimization.cesm import CESMOptimizationBackend
from pypeline import EnergySystemBuilder, TechnologyRegistry
from pypeline.energy_system.rule_book import EnergySystemRuleBook
from pypeline.energy_system.scenario import Scenario


def confirm_continue(polygon_plot_path: Path) -> bool:
    """Prompt user to continue after plot review."""
    if not sys.stdin.isatty():
        return True
    user_choice = input(f"Review {polygon_plot_path.name}. Press Enter to start Optimization, or type 's' to stop: ").strip().lower()
    if user_choice in {"stop", "s"}:
        print("Aborting before optimization at user's request.")
        return False
    return True


def create_local_data_registry(heat_demand_file: Path, heating_shares_file: Path):
    """Return a local-mode data registry for heat inputs."""
    if not heat_demand_file.exists():
        raise FileNotFoundError(f"Missing heat demand file: {heat_demand_file}")
    if not heating_shares_file.exists():
        raise FileNotFoundError(f"Missing heating shares file: {heating_shares_file}")
    return get_default_data_registry(
        mode="local",
        local_heat_demand_file=heat_demand_file,
        local_heating_shares_file=heating_shares_file,
    )


def ensure_pythonpath(project_root: Path) -> None:
    """Ensure project_root is on PYTHONPATH."""
    existing_py_path = os.environ.get("PYTHONPATH")
    if existing_py_path:
        os.environ["PYTHONPATH"] = f"{project_root}{os.pathsep}{existing_py_path}"
    else:
        os.environ["PYTHONPATH"] = str(project_root)


def create_cesm_backend(project_root: Path, *, model_name: str, scenario_name: str, tss_name: str, scenario, demand_name: str) -> CESMOptimizationBackend:
    """Configure and return a CESMBackend instance."""
    workdir = (project_root / "CESM").resolve()
    ensure_pythonpath(project_root)
    return CESMOptimizationBackend(
        workdir=str(workdir),
        cli=[sys.executable, "-m", "pypeline.optimization.cesm.cli"],
        run_args=["--workdir", str(workdir), "-m", model_name, "-s", scenario_name],
        run_subdir=f"{model_name}-{scenario_name}",
        results_db_name="db.sqlite",
        write_inputs=True,
        model_name=model_name,
        scenario_name=scenario_name,
        tss_name=tss_name,
        scenario=scenario,
        demand_name=demand_name,
    )


def years_for_scenario(scenario) -> list[int]:
    """Return scenario year list for plotting."""
    # Should be replaced by a property on Scenario in near future.
    return list(range(int(scenario.start_year), int(scenario.end_year) + 1, int(scenario.year_gap)))


def plot_mix_results(plotter, results: dict, years: list[int], plots_dir: Path) -> None:
    """Save default mix plots to `plots_dir`."""
    mix_plot_paths = plotter.save_default_mix_plots(results, years=years, plots_dir=plots_dir)
    technology_plot_path = mix_plot_paths["technology"]
    print(f"Saved technology plot: {technology_plot_path}")
    plt.show()


def show_cesm_sankey(project_root: Path, results: dict, years: list[int]) -> None:
    """Show CESM Sankey plots from results DB."""
    from cesm.core.plotter import Plotter
    from cesm.core.data_access import DAO

    db_path = Path(results["db"])
    if not db_path.is_absolute():
        db_path = project_root / db_path
    print(f"Using DB: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        dao = DAO(conn)
        plotter = Plotter(dao)
        for year in years:
            sankey_fig = plotter.plot_sankey(year=year)
            sankey_fig.show()
    finally:
        conn.close()


def build_energy_system(*, model_name: str, region_topologies: list, street_network, heat_demand_file: Path, heating_shares_file: Path, region_builder_config_overrides: dict | None, rulebook: EnergySystemRuleBook | None = None):
    """Build energy system from a list of region topology graphs and local data."""
    tech_reg = TechnologyRegistry()
    tech_reg.load_from_default()

    esb = EnergySystemBuilder(energy_system_name=model_name)
    esb.set_polygons(polygons)
    if district_street_segments is not None:
        esb.set_district_street_segments(district_street_segments)
    esb.set_demands(default=True)
    esb.set_technology_registry(tech_reg)
    data_reg = create_local_data_registry(heat_demand_file, heating_shares_file)
    esb.set_data_registry(data_reg)
    esb.set_default_region_builder_config()
    if region_builder_config_overrides:
        esb.region_builder_config.update(region_builder_config_overrides)
    esb.set_energy_system_rule_book(rulebook if rulebook is not None else EnergySystemRuleBook())
    return esb.build()


def build_scenario(*, model_name: str, scenario_name: str, tss_name: str, start_year: int, end_year: int, year_gap: int, retain_existing_output_drop_per_year: float, lockout_years: int) -> Scenario:
    """Create a Scenario for optimization runs."""
    run_name = f"{model_name}-{scenario_name}"
    return Scenario(
        name=run_name,
        start_year=start_year,
        end_year=end_year,
        year_gap=year_gap,
        tss=tss_name,
        retain_existing_output_drop_per_year=retain_existing_output_drop_per_year,
        lockout_years=lockout_years,
    )
