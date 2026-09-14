# coding=utf-8
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from gurobipy import GRB

from cesm.core.input_parser import Parser
from cesm.core.model import Model

from geopipe.energy_system import EnergySystem, Scenario
from geopipe.optimization.cesm.profiles import profile_names, write_profiles, copy_static_timeseries
from geopipe.optimization.cesm.techmap import Techmap, create_techmap
from geopipe.optimization.cesm.result_parser import CESMResultsParser, CESMSolution
from geopipe.optimization.solver import OptimizationBackend
from geopipe.optimization.resolved_system import ResolvedSystem, resolve_system

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

    def solve(self, energy_system: EnergySystem, scenario: Scenario, mip_gap: Optional[float] = None, lp_file: bool = False) -> CESMSolution:
        run_name = f"{energy_system.name}_{scenario.name}"
        db_path = self.output_dir / f"{run_name}.sqlite"

        resolved = resolve_system(energy_system,scenario)
        names = profile_names(resolved.demands)
        techmap = create_techmap(resolved, names)
        techmap.to_excel(self.output_dir / f"{run_name}.xlsx")
        timeseries_dir = self._write_timeseries(resolved, names, techmap)

        self._run_cesm(energy_system_name = energy_system.name, scenario_name=scenario.name, db_path=db_path, run_name=run_name, mip_gap=mip_gap, lp_file=lp_file, timeseries_dir=timeseries_dir)
        return CESMResultsParser(db_path=db_path, energy_system=energy_system, scenario=scenario).parse()

    def _write_timeseries(self, resolved: ResolvedSystem, names: dict[str, str], techmap: Techmap) -> Path:
        """Write the demand profiles and copy the static timeseries the techmap references into one freshly
        cleared folder, ``output_dir/profiles``, as CESM reads all timeseries from a single folder. Returns the
        folder."""
        directory = self.output_dir / "profiles"
        write_profiles(resolved.demands, names, directory)
        referenced = set(techmap.TSS["TSS_name"]) | set(
            techmap.ConversionSubProcess[["output_profile", "availability_profile"]].stack().dropna())
        copy_static_timeseries(referenced - set(names.values()), self.timeseries_dir, directory)
        return directory

    def _run_cesm(self, energy_system_name: str, scenario_name: str, db_path: Path, run_name: str, mip_gap: Optional[float], lp_file: bool, timeseries_dir: Path) -> None:
        start = time.perf_counter()
        logger.info("Running CESM optimization for energy system '%s', scenario '%s'", energy_system_name, scenario_name)
        conn = sqlite3.connect(":memory:")
        parser = Parser(run_name, techmap_dir_path=self.output_dir, ts_dir_path=timeseries_dir, db_conn=conn, scenario=scenario_name)
        parser.parse()
        model = Model(conn=conn)
        if lp_file:
            lp_path = self.output_dir / f"{run_name}.lp"
            model.model.write(str(lp_path))
            logger.info("Wrote CESM model LP file: %s", lp_path)

        model.solve(mip_gap=mip_gap)

        grb_model = model.model
        logger.info(grb_model.printQuality())
        status = int(grb_model.Status)
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
        logger.info("Ran CESM in %.2f seconds", time.perf_counter() - start)
        logger.info("CESM output written to database: %s", db_path)