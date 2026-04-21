"""CESMOptimizationBackend — the main entry point for CESM-based optimization.

Implements the :class:`~pypeline.optimization.solver.OptimizationBackend`
interface by orchestrating the full solve pipeline::

    CESMOptimizationBackend.solve(energy_system, scenario)
        │
        ├─ input_writer.write_cesm_inputs_from_energy_system()
        │       writes techmap XLSX to output_dir
        │       writes timeseries TXT to timeseries_dir
        │
        ├─ _run_cesm()
        │       calls the CESM Python API directly (Parser → Model → save_output)
        │       writes db.sqlite to output_dir / run_subdir
        │
        ├─ result_parser.backfill_missing_commodity_timeseries()
        │       fills gaps in output_co_y_t from timeseries
        │
        └─ result_parser.parse_cesm_outputs()
                returns CESMResults → Solution
"""
from __future__ import annotations
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

from cesm.core.input_parser import Parser
from cesm.core.model import Model

try:
    from gurobipy import GRB  # type: ignore
except Exception:  # pragma: no cover
    GRB = None  # type: ignore

from pypeline.energy_system.core import EnergySystem, Scenario
from pypeline.optimization.cesm.input_writer import write_cesm_inputs_from_energy_system
from pypeline.optimization.cesm.result_parser import (
    backfill_missing_commodity_timeseries,
    parse_cesm_outputs,
)
from pypeline.optimization.solver import OptimizationBackend, Solution

logger = logging.getLogger(__name__)


def _status_text(status: int) -> str:
    if GRB is None:
        return f"status={status}"
    mapping = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.UNBOUNDED: "UNBOUNDED",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.NUMERIC: "NUMERIC",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
    }
    return mapping.get(status, f"status={status}")


class CESMOptimizationBackend(OptimizationBackend):
    """Optimization backend that writes CESM inputs, runs the CESM solver, and parses results."""

    def __init__(
        self,
        model_name: Optional[str],
        scenario_name: Optional[str],
        tss_name: Optional[str],
        timeseries_dir: str | Path,
        output_dir: str | Path,
        dt_hours: int = 4,
        run_subdir: Optional[str] = None,
        results_db_name: str = "db.sqlite",
        write_inputs: bool = True,
        scenario: Scenario | None = None,
        demand_name: str = "residential_heat",
        retain_existing_output_factor: float | None = None,
        retain_existing_output_years_factor: float | None = None,
        retain_existing_output_schedule: Optional[List[float]] = None,
    ):
        self.timeseries_dir = Path(timeseries_dir)
        self.output_dir = Path(output_dir)
        self.timeseries_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

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
    def solve(self, energy_system: EnergySystem, scenario: Scenario | None = None) -> Solution:
        if not isinstance(energy_system, EnergySystem):
            raise TypeError("CESMOptimizationBackend.solve expects an EnergySystem")

        logger.debug("solve: energy_system commodity_config=%s", energy_system.commodity_config)

        scenario_obj = scenario or self.scenario
        if scenario_obj is None:
            raise ValueError("scenario is required to write CESM inputs")

        self._materialize_inputs_from_energy_system(energy_system, scenario_obj)
        self._run_cesm()
        db_path = self._expected_run_db()
        backfill_missing_commodity_timeseries(db_path)
        results = parse_cesm_outputs(db_path)
        return Solution(energy_system=energy_system, scenario=scenario_obj, results=results)

    # Internal helpers -------------------------------------------------------
    def _expected_run_db(self) -> Path:
        if not self.run_subdir:
            raise ValueError("run_subdir is not set (expected '{model}-{scenario}').")
        return self.output_dir / self.run_subdir / self.results_db_name

    def _materialize_inputs_from_energy_system(self, energy_system: EnergySystem, scenario: Scenario) -> None:
        if not self.write_inputs:
            return

        write_cesm_inputs_from_energy_system(
            energy_system,
            scenario,
            techmap_dir=self.output_dir,
            timeseries_dir=self.timeseries_dir,
            model_name=self.model_name,
            scenario_name=self.scenario_name,
            tss_name=self.tss_name,
            dt_hours=self.dt_hours,
            retain_existing_output_factor=self.retain_existing_output_factor,
            retain_existing_output_years_factor=self.retain_existing_output_years_factor,
            retain_existing_output_schedule=self.retain_existing_output_schedule,
        )

    def _run_cesm(self) -> None:
        if not self.run_subdir:
            raise ValueError("run_subdir is not set (expected '{model}-{scenario}').")

        db_dir = self.output_dir / self.run_subdir
        db_path = db_dir / self.results_db_name
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(":memory:")
        parser = Parser(self.model_name, techmap_dir_path=self.output_dir, ts_dir_path=self.timeseries_dir, db_conn=conn, scenario=self.scenario_name)
        parser.parse()
        model = Model(conn=conn)
        model.solve()

        grb_model = getattr(model, "model", None)
        status = int(getattr(grb_model, "Status", -1))
        if GRB is not None and status not in (GRB.OPTIMAL, GRB.SUBOPTIMAL):
            logger.error("CESM optimization did not produce a solution: %s", _status_text(status))
            if status == GRB.INFEASIBLE:
                try:
                    grb_model.computeIIS()
                    iis_path = db_dir / "model_iis.ilp"
                    grb_model.write(str(iis_path))
                    logger.error("Wrote IIS file: %s", iis_path)
                except Exception as iis_exc:  # pragma: no cover
                    logger.error("IIS computation failed: %s", iis_exc)
            conn.close()
            raise RuntimeError(f"CESM optimization failed with status {_status_text(status)}")

        model.save_output()
        if db_path.exists():
            db_path.unlink()
        disk = sqlite3.connect(str(db_path))
        conn.backup(disk)
        disk.close()
        conn.close()