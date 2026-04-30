# coding=utf-8
import logging
import sqlite3
import time
from pathlib import Path

from gurobipy import GRB

from cesm.core.input_parser import Parser
from cesm.core.model import Model

from pypeline.energy_system import EnergySystem, Scenario
from pypeline.optimization.cesm.techmap import create_techmap
from pypeline.optimization.cesm.result_parser import CESMResultsParser, CESMSolution
from pypeline.optimization.solver import OptimizationBackend
from pypeline.optimization.resolved_system import resolve_system

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
        output_dir: str | Path
    ):
        self.timeseries_dir = Path(timeseries_dir)
        self.output_dir = Path(output_dir)

        self.timeseries_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def solve(self, energy_system: EnergySystem, scenario: Scenario) -> CESMSolution:
        run_name = f"{energy_system.name}_{scenario.name}"
        db_path = self.output_dir / f"{run_name}.sqlite"

        resolved = resolve_system(energy_system,scenario)
        techmap = create_techmap(resolved)
        techmap.to_excel(self.output_dir / f"{run_name}.xlsx")

        self._run_cesm(energy_system_name = energy_system.name, scenario_name=scenario.name, db_path=db_path, run_name=run_name)
        return CESMResultsParser(db_path=db_path, energy_system=energy_system, scenario=scenario).parse()

    def _run_cesm(self, energy_system_name: str, scenario_name: str, db_path: Path, run_name: str) -> None:
        start = time.perf_counter()
        logger.info("Running CESM optimization for energy system '%s', scenario '%s'", energy_system_name, scenario_name)
        logger.info("CESM techmap input directory: %s", self.output_dir)
        conn = sqlite3.connect(":memory:")
        parser = Parser(run_name, techmap_dir_path=self.output_dir, ts_dir_path=self.timeseries_dir, db_conn=conn, scenario=scenario_name)
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
                    iis_path = self.output_dir / "model_iis.ilp"
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
        logger.info("Finished CESM optimization in %.2f seconds", time.perf_counter() - start)
        logger.info("CESM output written to database: %s", db_path)