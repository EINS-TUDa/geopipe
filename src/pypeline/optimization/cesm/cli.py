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
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _validate_cesm_paths(cesm_dir: Path, model_name: str) -> tuple[Path, Path, Path]:
    data_dir = cesm_dir / "Data"
    techmap_dir = data_dir / "Techmap"
    ts_dir = data_dir / "TimeSeries"
    runs_dir = cesm_dir / "Runs"
    workbook = techmap_dir / f"{model_name}.xlsx"
    required = [
        ("CESM folder", cesm_dir),
        ("CESM core folder", cesm_dir / "core"),
        ("CESM Data directory", data_dir),
        ("Techmap directory", techmap_dir),
    ]
    missing: list[tuple[str, Path]] = [(desc, path) for desc, path in required if not path.exists()]
    if not workbook.exists():
        missing.append(("Techmap workbook", workbook))
    if missing:
        for desc, path in missing:
            logger.error("Missing %s: %s", desc, path)
        raise FileNotFoundError("Missing CESM resources")
    return techmap_dir, ts_dir, runs_dir


def main() -> int:
    import sys as _sys
    cwd = Path.cwd().resolve()
    if (cwd / "core").exists():
        cesm_root = cwd
    elif (cwd / "CESM" / "core").exists():
        cesm_root = (cwd / "CESM").resolve()
    else:
        cesm_root = None
    if cesm_root:
        paths_to_add = {str(cesm_root), str(cesm_root / "core"), str(cesm_root.parent)}
        for p in list(paths_to_add):
            if p not in _sys.path:
                _sys.path.insert(0, p)

    from cesm.core.input_parser import Parser  # type: ignore
    from cesm.core.model import Model  # type: ignore

    ap = argparse.ArgumentParser(description="Invoke CESM run using unified plugin.")
    ap.add_argument("--workdir", default=".", help="Project root that contains the CESM/ folder (default: .)")
    ap.add_argument("-m", "--model", required=True, help="Techmap XLSX name (without .xlsx)")
    ap.add_argument("-s", "--scenario", required=True, help="Scenario name as in the XLSX Scenario sheet")
    args = ap.parse_args()
    root = Path(args.workdir).resolve()
    cesm_dir = root / "CESM" if (root / "CESM" / "core").exists() else root
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
