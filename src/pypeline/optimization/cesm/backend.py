"""CESMOptimizationBackend — the main entry point for CESM-based optimization.

Implements the :class:`~pypeline.optimization.solver.OptimizationBackend`
interface by orchestrating the full solve pipeline::

    CESMOptimizationBackend.solve(energy_system, scenario)
        │
        ├─ input_writer.write_cesm_inputs_from_energy_system()
        │       builds OptimizationContext, writes XLSX + timeseries TXT
        │
        ├─ _run_cli()
        │       invokes the CESM solver as a subprocess
        │
        ├─ result_parser.backfill_missing_commodity_timeseries()
        │       fills gaps in output_co_y_t from subprocess timeseries
        │
        └─ result_parser.parse_cesm_outputs()
                returns CESMResults → Solution
"""
from __future__ import annotations
import json
import logging
import subprocess
from pathlib import Path
from typing import List, Optional

from pypeline.energy_system.core import EnergySystem, Scenario
from pypeline.optimization.cesm.input_writer import write_cesm_inputs_from_energy_system
from pypeline.optimization.cesm.result_parser import (
    backfill_missing_commodity_timeseries,
    parse_cesm_outputs,
)
from pypeline.optimization.solver import OptimizationBackend, Solution

logger = logging.getLogger(__name__)


class CESMOptimizationBackend(OptimizationBackend):
    """Optimization backend that writes CESM inputs, invokes CESM, and parses results."""

    def __init__(
        self,
        model_name: Optional[str],
        scenario_name: Optional[str],
        tss_name: Optional[str],
        dt_hours: int = 4,
        workdir: str | Path = "CESM",
        cli: Optional[List[str]] = None,
        run_args: Optional[List[str]] = None,
        run_subdir: Optional[str] = None,
        results_db_name: str = "db.sqlite",
        write_inputs: bool = True,
        scenario: Scenario | None = None,
        demand_name: str = "residential_heat",
        retain_existing_output_factor: float | None = None,
        retain_existing_output_years_factor: float | None = None,
        retain_existing_output_schedule: Optional[List[float]] = None,
    ):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

        self.cli = cli or ["env\\Scripts\\python.exe", "-m", "main", "run"]
        self.run_args = run_args or []

        self.model_name = model_name
        self.scenario_name = scenario_name
        self.tss_name = tss_name
        self.dt_hours = int(dt_hours)
        self.run_subdir = run_subdir or (f"{self.model_name}-{self.scenario_name}" if self.model_name and self.scenario_name else None)
        self.results_db_name = results_db_name

        self.write_inputs = write_inputs
        self.scenario = scenario
        self.demand_name = demand_name
        self.retain_existing_output_factor = retain_existing_output_factor
        self.retain_existing_output_years_factor = retain_existing_output_years_factor
        self.retain_existing_output_schedule = retain_existing_output_schedule

    # OptimizationBackend API ------------------------------------------------
    def solve(self, energy_system: EnergySystem, scenario: Scenario | None = None, demand_name: str | None = None) -> Solution:
        if not isinstance(energy_system, EnergySystem):
            raise TypeError("CESMOptimizationBackend.solve expects an EnergySystem")

        logger.debug("solve: energy_system commodity_config=%s", energy_system.commodity_config)

        scenario_obj = scenario or self.scenario
        if scenario_obj is None:
            raise ValueError("scenario is required to write CESM inputs")
        demand = demand_name or self.demand_name or "residential_heat"

        self._materialize_inputs_from_energy_system(energy_system, scenario_obj, demand)
        self._run_cli()
        db_path = self._expected_run_db()
        if not db_path.exists():
            raise FileNotFoundError(
                f"Expected DB not found: {db_path}\n"
                f"Check logs: {self.workdir / 'cesm_stdout.log'}, {self.workdir / 'cesm_stderr.log'} "
                f"and command {self.workdir / 'cesm_cmd.txt'}"
            )
        backfill_missing_commodity_timeseries(db_path)
        results = parse_cesm_outputs(db_path)
        return Solution(energy_system=energy_system, scenario=scenario_obj, results=results)

    # Internal helpers -------------------------------------------------------
    def _expected_run_db(self) -> Path:
        if not self.run_subdir:
            raise ValueError("run_subdir is not set (expected '{model}-{scenario}').")
        primary = self.workdir / "Runs" / self.run_subdir / self.results_db_name
        return primary

    def _materialize_inputs_from_energy_system(self, energy_system: EnergySystem, scenario: Scenario, demand_name: str) -> None:
        if not self.write_inputs:
            return

        write_cesm_inputs_from_energy_system(
            energy_system,
            scenario,
            workdir=self.workdir,
            model_name=self.model_name,
            scenario_name=self.scenario_name,
            tss_name=self.tss_name,
            dt_hours=self.dt_hours,
            demand_name=demand_name,
            retain_existing_output_factor=self.retain_existing_output_factor,
            retain_existing_output_years_factor=self.retain_existing_output_years_factor,
            retain_existing_output_schedule=self.retain_existing_output_schedule,
        )

    def _run_cli(self, extra_args: list[str] | None = None) -> None:
        exe = self.cli[0]
        exe_path = Path(exe)
        if not exe_path.is_absolute():
            exe_path = (self.workdir / exe_path).resolve()
        if not exe_path.exists():
            raise FileNotFoundError(
                f"CESM executable not found: {exe_path}\nWorkdir: {self.workdir.resolve()}\nCLI: {self.cli}"
            )
        cmd = [str(exe_path), *self.cli[1:], *self.run_args]
        if extra_args:
            cmd.extend(extra_args)
        (self.workdir / "cesm_cmd.txt").write_text(json.dumps(cmd, indent=2))
        cp = subprocess.run(cmd, cwd=self.workdir, text=True)
        if cp.returncode != 0:
            raise RuntimeError(f"CESM failed (exit {cp.returncode}). See logs in workdir")
