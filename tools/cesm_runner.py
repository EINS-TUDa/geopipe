from __future__ import annotations

import argparse
import sqlite3
import sys
import types
from pathlib import Path

def _ensure_cesm_package(cesm_dir: Path) -> None:
    """
    resolves files under the CESM folder without importing CESM/cesm.py.
    """
    if "cesm" in sys.modules and getattr(sys.modules["cesm"], "__path__", None):
        return
    shim = types.ModuleType("cesm")
    shim.__file__ = str(cesm_dir / "__init__.py")
    shim.__path__ = [str(cesm_dir)]
    sys.modules["cesm"] = shim

def must_exist(p: Path, what: str) -> None:
    if not p.exists():
        print(f"ERROR: Missing {what}: {p}", file=sys.stderr)
        sys.exit(2)

def main() -> int:
    ap = argparse.ArgumentParser(description="Thin runner to invoke CESM without touching its code.")
    ap.add_argument("--workdir", default=".", help="Project root that contains the CESM/ folder (default: .)")
    ap.add_argument("-m", "--model", required=True, help="Techmap XLSX name (without .xlsx)")
    ap.add_argument("-s", "--scenario", required=True, help="Scenario name as in the XLSX Scenario sheet")
    args = ap.parse_args()

    root = Path(args.workdir).resolve()
    cesm_dir = root / "CESM" if (root / "CESM" / "core").exists() else root
    core_dir = cesm_dir / "core"

    if not core_dir.exists():
        print(f"ERROR: Missing CESM folder: {cesm_dir}")
        return 2

    sys.path.insert(0, str(cesm_dir))
    _ensure_cesm_package(cesm_dir)

    from core.input_parser import Parser
    from core.model import Model

    data_dir    = cesm_dir / "Data"
    techmap_dir = data_dir / "Techmap"
    ts_dir      = data_dir / "TimeSeries"
    runs_dir    = cesm_dir / "Runs"

    model_name    = args.model
    scenario_name = args.scenario
    run_name      = f"{model_name}-{scenario_name}"
    db_dir        = runs_dir / run_name
    db_path       = db_dir / "db.sqlite"

    must_exist(cesm_dir, "CESM folder")
    must_exist(techmap_dir / f"{model_name}.xlsx", "Techmap workbook")
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
        print(f"Deleted previous DB: {db_path}")
    disk = sqlite3.connect(str(db_path))
    conn.backup(disk)
    disk.close()
    conn.close()
    print(f"DB written: {db_path}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
