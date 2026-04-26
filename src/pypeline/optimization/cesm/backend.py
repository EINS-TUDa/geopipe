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
from gurobipy import GRB

from cesm.core.input_parser import Parser
from cesm.core.model import Model

from pypeline.energy_system_my import EnergySystem, Scenario
from pypeline.optimization.cesm.result_parser import (
    backfill_missing_commodity_timeseries,
    parse_cesm_outputs,
)
from pypeline.optimization.solver import OptimizationBackend, Solution
from pypeline.optimization.resolved_system import resolve_system
from pypeline.optimization.cesm.input_writer import _write_cesm_inputs

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
        timeseries_dir: str | Path,
        output_dir: str | Path,
        results_db_name: str = "db.sqlite",
        demand_name: str = "residential_heat",
        retain_existing_output_factor: float | None = None,
        retain_existing_output_years_factor: float | None = None,
        retain_existing_output_schedule: Optional[List[float]] = None,
    ):
        self.timeseries_dir = Path(timeseries_dir)
        self.output_dir = Path(output_dir)
        self.timeseries_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.run_subdir = None
        self.results_db_name = results_db_name

        self.demand_name = demand_name
        self.retain_existing_output_factor = retain_existing_output_factor
        self.retain_existing_output_years_factor = retain_existing_output_years_factor
        self.retain_existing_output_schedule = retain_existing_output_schedule

    # OptimizationBackend API ------------------------------------------------
    def solve(self, energy_system: EnergySystem, scenario: Scenario | None = None) -> Solution:
        self.run_subdir = f"{energy_system.name}-{scenario.name}"
        self._materialize_inputs_from_energy_system(energy_system, scenario)
        self._run_cesm(energy_system_name = energy_system.name, scenario_name=scenario.name)
        db_path = self.output_dir / self.run_subdir / self.results_db_name
        backfill_missing_commodity_timeseries(db_path)
        results = parse_cesm_outputs(db_path)
        return Solution(energy_system=energy_system, scenario=scenario, results=results)

    def _materialize_inputs_from_energy_system(self, energy_system: EnergySystem, scenario: Scenario) -> None:
        resolved = resolve_system(
            energy_system,
            scenario,
            retain_existing_output_schedule=self.retain_existing_output_schedule,
        )
        _write_cesm_inputs(
            resolved,
            techmap_dir=self.output_dir,
            timeseries_dir=self.timeseries_dir,
            model_name=energy_system.name,
            scenario_name=scenario.name,
            tss_name=scenario.tss,
            dt_hours=scenario.dt_hours,
            retain_existing_output_factor=self.retain_existing_output_factor,
            retain_existing_output_years_factor=self.retain_existing_output_years_factor,
        )

    def _run_cesm(self, energy_system_name: str, scenario_name: str) -> None:
        db_dir = self.output_dir / self.run_subdir
        db_path = db_dir / self.results_db_name
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(":memory:")
        parser = Parser(energy_system_name, techmap_dir_path=self.output_dir, ts_dir_path=self.timeseries_dir, db_conn=conn, scenario=scenario_name)
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