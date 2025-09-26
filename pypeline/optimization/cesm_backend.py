# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pypeline.optimization.solver import OptimizationModel, Solution, Results
from pypeline.optimization.cesm_to_om_adapter import OMContext

import pandas as pd

InputWriter = Callable[[OMContext, Path, str, str, str], None]

class CESMBackend(OptimizationModel):
    """
    Thin adapter that:
      1) materializes OM -> CESM input files into CESM/Data/*
      2) invokes CESM's CLI
      3) reads the expected run DB: CESM/Runs/{model}-{scenario}/db.sqlite
    """

    def __init__(
        self,
        workdir: str | Path = "CESM",
        cli: Optional[List[str]] = None,             
        run_args: Optional[List[str]] = None,
        run_subdir: Optional[str] = None,
        results_db_name: str = "db.sqlite",
        input_writer: Optional[Callable[[OMContext, Path, str, str, str], None]] = None,                                    
        model_name: Optional[str] = None,
        scenario_name: Optional[str] = None,
        tss_name: Optional[str] = None,
        write_inputs: bool = True,
    ):
        super().__init__(conversion_sub_processes=None,conversion_processes=None, commodities=None, tss=None)
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

        self.cli = cli or ["env\\Scripts\\python.exe", "-m", "main", "run"]
        self.run_args = run_args or []

        self.model_name = model_name
        self.scenario_name = scenario_name
        self.tss_name = tss_name
        self.run_subdir = run_subdir or ( f"{self.model_name}-{self.scenario_name}" if self.model_name and self.scenario_name else None)
        self.results_db_name = results_db_name

        self.input_writer = input_writer
        self.write_inputs = write_inputs

    def optimize(self, model: OMContext) -> Solution:
        
        self._materialize_inputs(model)
        self._run_cli()

        # Read the expected DB only
        db_path = self._expected_run_db()
        if not db_path.exists():
            raise FileNotFoundError(
                f"Expected DB not found: {db_path}\n"
                f"Check logs: {self.workdir / 'cesm_stdout.log'}, {self.workdir / 'cesm_stderr.log'} "
                f"and command {self.workdir / 'cesm_cmd.txt'}"
            )

        results = self._parse_outputs(db_path)
        sol = Solution()
        sol.results = results
        return sol

    def _expected_run_db(self) -> Path:
        """
        Only accept the DB produced for this run:
          CESM/Runs/{model_name}-{scenario_name}/db.sqlite
        """
        if not self.run_subdir:
            raise ValueError("run_subdir is not set (expected '{model}-{scenario}').")
        candidate = self.workdir / "Runs" / self.run_subdir / self.results_db_name
        return candidate

    def _materialize_inputs(self, ctx: OMContext) -> None:
        """
        Write Techmap + TSS into CESM/Data/ for model/scenario.
        This does not modify CESM code; it only writes data files.
        """
        if not self.write_inputs:
            return
        if not (self.input_writer and self.model_name and self.scenario_name and self.tss_name):
            return

        self.input_writer(
            ctx,
            self.workdir,
            self.model_name,
            self.scenario_name,
            self.tss_name,
        )

    def _run_cli(self) -> None:
        """
        Launch CESM in self.workdir with the provided CLI args.
        """
        exe = self.cli[0]
        exe_path = Path(exe)
        if not exe_path.is_absolute():
            exe_path = (self.workdir / exe_path).resolve()
        if not exe_path.exists():
            raise FileNotFoundError(
                f"CESM executable not found: {exe_path}\n"
                f"Workdir: {self.workdir.resolve()}\n"
                f"CLI: {self.cli}"
            )

        cmd = [str(exe_path), *self.cli[1:], *self.run_args]

        (self.workdir / "cesm_cmd.txt").write_text(json.dumps(cmd, indent=2))

        cp = subprocess.run(cmd, cwd=self.workdir, capture_output=True, text=True)

        (self.workdir / "cesm_stdout.log").write_text(cp.stdout or "")
        (self.workdir / "cesm_stderr.log").write_text(cp.stderr or "")

        if cp.returncode != 0:
            raise RuntimeError(
                f"CESM failed (exit {cp.returncode}). "
                f"See {self.workdir / 'cesm_stderr.log'} and {self.workdir / 'cesm_stdout.log'}"
            )


    def _parse_outputs(self, db_path: Path) -> Results:
        """Reads the CESM outputs and transforms it into the pypeline results format, as defined in Results."""
        with sqlite3.connect(db_path) as con:
            cur = con.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [t[0] for t in cur.fetchall()]

            # helper maps
            def kv(table: str, k: str, v: str) -> dict:
                """Returns a dict mapping k -> v for all rows in table."""
                cur.execute(f"SELECT {k}, {v} FROM {table}")
                return {row[0]: row[1] for row in cur.fetchall()}
            year_map = kv("year", "id", "value") if "year" in tables else {}
            com_map  = kv("commodity", "id", "name") if "commodity" in tables else {}
            tech_map: dict[int, str] = {}
            cur.execute("""
                            SELECT cs.id, cp.name
                            FROM conversion_subprocess cs
                            JOIN conversion_process cp ON cs.cp_id = cp.id
                        """)
            tech_map = {row[0]: row[1] for row in cur.fetchall()}

            df_cs = pd.read_sql_query("""SELECT 
                            cs.id, 
                            cp.name AS cp,
                            co_in.name AS cin,
                            co_out.name AS cout
                        FROM conversion_subprocess cs
                        JOIN conversion_process cp ON cs.cp_id = cp.id
                        LEFT JOIN commodity co_in ON cs.cin_id = co_in.id
                        LEFT JOIN commodity co_out ON cs.cout_id = co_out.id""", con)


            # extract regions from name strings (e.g. "Demand_D0")
            def extract_region(name: str) -> Optional[int]:
                """Extracts the region number from a technology name with _D{region} suffix.
                Returns None if no region suffix is found."""
                match = re.search(r"_D(\d+)$", str(name))
                if match:
                    return int(match.group(1))
                return None

            def strip_region_suffix_from_name(name: str) -> str:
                """Strips the _D{region} suffix from a technology name."""
                return re.sub(r"_D\d+$", "", str(name))

            def output_cs_y_to_df(variable: str) -> pd.DataFrame:
                df = pd.read_sql_query(f"SELECT cs_id, y_id, {variable} FROM output_cs_y", con)
                df["y_id"] = df["y_id"].map(year_map)
                df = df.merge(df_cs[["id", "cp", "cin", "cout"]], left_on="cs_id", right_on="id", how="left")
                df = df.drop(columns=["cs_id", "id"])  # remove id columns
                df["r"] = df["cp"].apply(extract_region)
                df["cp"] = df["cp"].apply(strip_region_suffix_from_name)
                df = df.rename(columns={"y_id": "y"})
                return df

            df_yearly_energy_outputs = output_cs_y_to_df(variable="eouttot")
            df_active_capacities = output_cs_y_to_df(variable="cap_active")

            cur.execute("SELECT OPEX, CAPEX, TOTEX FROM output_global LIMIT 1")
            row = cur.fetchone()
            opex = float(row[0])
            capex = float(row[1])
            totex = float(row[2])

        results = Results(
            active_capacities= df_active_capacities,
            yearly_energy_outputs = df_yearly_energy_outputs,
            opex = opex,
            capex = capex,
            totex = totex,
        )
        return results


def write_cesm_inputs_minimal_from_om(om: OMContext, cesm_root: Path, model_name: str, scenario_name: str, tss_name: str,) -> None:
    """
    Minimal, generic writer:
      - creates a Techmap xlcx with the required sheets
      - fills Scenario from om.scenario (years, step, discount_rate, TSS)
      - emits whatever commodities / processes / subprocesses exist on om
        (missing params are left empty - CESM defaults apply)

    This is temp and thus light-weight.
    """

    tm_dir = cesm_root / "Data" / "Techmap"
    ts_dir = cesm_root / "Data" / "TimeSeries"
    tm_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)

    # Units (must include these names for CESM scaling)
    df_units = pd.DataFrame([
        {"quantity": "energy",       "scale_factor": 1.0, "input": "MWh",   "output": "MWh"},
        {"quantity": "power",        "scale_factor": 1.0, "input": "MW",    "output": "MW"},
        {"quantity": "cost_energy",  "scale_factor": 1.0, "input": "€/MWh", "output": "EUR/MWh"},
        {"quantity": "cost_power",   "scale_factor": 1.0, "input": "€/MW",  "output": "EUR/MW"},
        {"quantity": "co2_spec",     "scale_factor": 1.0, "input": "t/MWh", "output": "t/MWh"},
    ])

    # Scenario
    scen = getattr(om, "scenario", None)
    y0  = getattr(scen, "start_year", 2020)
    y1  = getattr(scen, "end_year",   y0)
    step = getattr(scen, "year_gap",  5)
    disc = getattr(scen, "discount_rate", 0.05) if hasattr(scen, "discount_rate") else 0.05
    df_scenario = pd.DataFrame([{
        "scenario_name": scenario_name,
        "from_year": y0,
        "until_year": y1,
        "year_step": step,
        "discount_rate": disc,
        "TSS": tss_name,
        "annual_co2_limit": None,
        "co2_price": None,
    }])

    df_tss = pd.DataFrame([{"TSS_name": tss_name, "dt": 1}])

    commodities = list(getattr(om, "commodities", []) or [])
    processes   = list(getattr(om, "conversion_processes", []) or [])
    df_co = pd.DataFrame([{"commodity_name": c, "order": None, "color": None} for c in commodities]) if commodities else pd.DataFrame(columns=["commodity_name","order","color"])
    df_cp = pd.DataFrame([{"conversion_process_name": p, "order": None, "color": None} for p in processes]) if processes else pd.DataFrame(columns=["conversion_process_name","order","color"])

    cs = list(getattr(om, "conversion_sub_processes", []) or [])

    base_cols = ["conversion_process_name", "commodity_in", "commodity_out", "scenario"]
    
    param_cols = [
        "spec_co2","efficiency","technical_lifetime","technical_availability","c_rate",
        "efficiency_charge","is_storage",
        "opex_cost_energy","opex_cost_power","capex_cost_power",
        "max_eout","min_eout","cap_min","cap_max","cap_res_max","cap_res_min",
        "out_frac_min","out_frac_max","in_frac_min","in_frac_max",
        "availability_profile","output_profile",
    ]
    rows = []
    for x in cs:
        row = {
            "conversion_process_name": x.get("cp") or x.get("process") or "",
            "commodity_in":            x.get("cin") or x.get("in") or "",
            "commodity_out":           x.get("cout") or x.get("out") or "",
            "scenario":                scenario_name,
        }
        for p in param_cols:
            row[p] = x.get(p, None)
        rows.append(row)
    df_cs = pd.DataFrame(rows, columns=base_cols + param_cols) if rows else pd.DataFrame(columns=base_cols + param_cols)

    xlsx = tm_dir / f"{model_name}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as xw:
        df_units.to_excel(xw, sheet_name="Units", index=False)
        df_scenario.to_excel(xw, sheet_name="Scenario", index=False)
        df_tss.to_excel(xw, sheet_name="TSS", index=False)
        df_co.to_excel(xw, sheet_name="Commodity", index=False)
        df_cp.to_excel(xw, sheet_name="ConversionProcess", index=False)
        # CESM expects CS sheet with two header rows it skips (Parser.skiprows=[1,2]).
        df_cs.to_excel(xw, sheet_name="ConversionSubProcess", index=False)

