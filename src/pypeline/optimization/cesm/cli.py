"""CLI entry point for running the CESM solver directly.

Used as a subprocess runner by :class:`~pypeline.optimization.cesm.backend.CESMOptimizationBackend`::

    python -m pypeline.optimization.cesm.cli --workdir . -m MyModel -s MyScenario

It expects the techmap XLSX and timeseries TXT to already exist on disk
(written by :mod:`pypeline.optimization.cesm.input_writer`), loads them via
the CESM parser, solves, and writes the result SQLite database to
``Runs/<model>-<scenario>/db.sqlite``.
"""
from __future__ import annotations
import argparse
import importlib
import logging
import sqlite3
import sys
from pathlib import Path

try:
    from gurobipy import GRB  # type: ignore
except Exception:  # pragma: no cover
    GRB = None  # type: ignore

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


def _validate_cesm_paths(cesm_dir: Path, model_name: str) -> tuple[Path, Path, Path]:
    data_dir = cesm_dir / "Data"
    techmap_dir = data_dir / "Techmap"
    ts_dir = data_dir / "TimeSeries"
    runs_dir = cesm_dir / "Runs"
    workbook = techmap_dir / f"{model_name}.xlsx"
    core_dir = cesm_dir / "src" / "cesm" / "core"
    required = [
        ("CESM folder", cesm_dir),
        ("CESM Data directory", data_dir),
        ("Techmap directory", techmap_dir),
        ("CESM core folder", core_dir),
        ("Techmap workbook", workbook),
    ]
    missing: list[tuple[str, Path]] = [(desc, path) for desc, path in required if not path.exists()]
    if missing:
        for desc, path in missing:
            logger.error("Missing %s: %s", desc, path)
        raise FileNotFoundError("Missing CESM resources")
    return techmap_dir, ts_dir, runs_dir


def main() -> int:
    import sys as _sys
    cwd = Path.cwd().resolve()
    if (cwd / "src" / "cesm" / "core").exists():
        cesm_root = cwd
    elif (cwd / "CESM" / "src" / "cesm" / "core").exists():
        cesm_root = (cwd / "CESM").resolve()
    else:
        cesm_root = None
    if cesm_root:
        paths_to_add = [str(cesm_root / "src"), str(cesm_root.parent), str(cesm_root)]
        for p in paths_to_add:
            if p not in _sys.path:
                _sys.path.insert(0, p)

    Parser = importlib.import_module("cesm.core.input_parser").Parser  # type: ignore[attr-defined]
    Model = importlib.import_module("cesm.core.model").Model  # type: ignore[attr-defined]

    ap = argparse.ArgumentParser(description="Invoke CESM run using unified plugin.")
    ap.add_argument("--workdir", default=".", help="Project root that contains the CESM/ folder (default: .)")
    ap.add_argument("-m", "--model", required=True, help="Techmap XLSX name (without .xlsx)")
    ap.add_argument("-s", "--scenario", required=True, help="Scenario name as in the XLSX Scenario sheet")
    args = ap.parse_args()
    root = Path(args.workdir).resolve()
    cesm_dir = root / "CESM" if (root / "CESM" / "src" / "cesm" / "core").exists() else root
    model_name = args.model
    scenario_name = args.scenario
    run_name = f"{model_name}-{scenario_name}"
    techmap_dir, ts_dir, runs_dir = _validate_cesm_paths(cesm_dir, model_name)
    db_dir = runs_dir / run_name
    db_path = db_dir / "db.sqlite"
    ts_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(exist_ok=True)
    db_dir.mkdir(exist_ok=True)
    conn = sqlite3.connect(":memory:")
    parser = Parser(model_name, techmap_dir_path=techmap_dir, ts_dir_path=ts_dir, db_conn=conn, scenario=scenario_name)
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
        logger.info("Deleted previous DB: %s", db_path)
    disk = sqlite3.connect(str(db_path))
    conn.backup(disk)
    disk.close()
    conn.close()
    logger.info("DB written: %s", db_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
