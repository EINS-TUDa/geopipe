# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pypeline.optimization.solver import OptimizationModel, Solution
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


    def _parse_outputs(self, db_path: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {"status": "ok", "db": str(db_path)}

        with sqlite3.connect(db_path) as con:
            cur = con.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [t[0] for t in cur.fetchall()]
            out["tables"] = tables

            def pragma_cols(table: str) -> list[str]:
                cur.execute(f"PRAGMA table_info({table})")
                return [row[1] for row in cur.fetchall()]

            columns = {t: pragma_cols(t) for t in tables}
            row_counts = {}
            for t in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    row_counts[t] = int(cur.fetchone()[0])
                except Exception:
                    row_counts[t] = -1
            out["columns"] = columns
            out["row_counts"] = row_counts

            # helper maps
            def kv(table: str, k: str, v: str) -> dict:
                cur.execute(f"SELECT {k}, {v} FROM {table}")
                return {row[0]: row[1] for row in cur.fetchall()}

            year_map = kv("year", "id", "value") if "year" in tables else {}
            com_map  = kv("commodity", "id", "name") if "commodity" in tables else {}

            tech_map: dict[int, str] = {}
            if "conversion_subprocess" in tables and "conversion_process" in tables:
                cur.execute("""
                    SELECT cs.id, cp.name
                    FROM conversion_subprocess cs
                    JOIN conversion_process cp ON cs.cp_id = cp.id
                """)
                tech_map = {row[0]: row[1] for row in cur.fetchall()}

            kpis: Dict[str, Any] = {}

            if "output_y" in tables and all(c in columns["output_y"] for c in ("y_id", "total_annual_co2_emission")):
                cur.execute(""" SELECT y_id, SUM(total_annual_co2_emission) FROM output_y GROUP BY y_id """)
                kpis["emissions_by_year"] = {str(year_map.get(y, y)): float(v) for (y, v) in cur.fetchall()}

            if "output_co_y_t" in tables and all(c in columns["output_co_y_t"] for c in ("co_id","y_id","enetgen","enetcons")):
                cur.execute(""" SELECT co_id, y_id, SUM(enetgen) AS gen, SUM(enetcons) AS cons FROM output_co_y_t GROUP BY co_id, y_id""")
                gen = {}
                cons = {}
                for co, y, g, c in cur.fetchall():
                    cn = str(com_map.get(co, co))
                    yn = str(year_map.get(y, y))
                    gen.setdefault(cn, {})[yn] = float(g)
                    cons.setdefault(cn, {})[yn] = float(c)
                kpis["gen_by_commodity_year"]  = gen
                kpis["cons_by_commodity_year"] = cons

                bal: Dict[str, float] = {}
                for c, per_year in gen.items():
                    for yname, gval in per_year.items():
                        bal[yname] = bal.get(yname, 0.0) + gval
                for c, per_year in cons.items():
                    for yname, cval in per_year.items():
                        bal[yname] = bal.get(yname, 0.0) - cval
                kpis["net_energy_balance_by_year"] = bal

            if "output_cs_y" in tables and all(c in columns["output_cs_y"] for c in ("cs_id","y_id","eouttot")):
                cur.execute(""" SELECT cs_id, y_id, SUM(eouttot) FROM output_cs_y GROUP BY cs_id, y_id """)
                e_by_tech = {}
                for cs, y, val in cur.fetchall():
                    tn = str(tech_map.get(cs, cs))
                    yn = str(year_map.get(y, y))
                    e_by_tech.setdefault(tn, {})[yn] = float(val)
                kpis["energy_by_tech_year"] = e_by_tech

            if "output_cs_y" in tables and all(c in columns["output_cs_y"] for c in ("cs_id","y_id","cap_active","cap_new")):
                cur.execute(""" SELECT cs_id, y_id, SUM(cap_active), SUM(cap_new) FROM output_cs_y GROUP BY cs_id, y_id """)
                cap_active = {}
                cap_new = {}
                for cs, y, a, n in cur.fetchall():
                    tn = str(tech_map.get(cs, cs))
                    yn = str(year_map.get(y, y))
                    cap_active.setdefault(tn, {})[yn] = float(a)
                    cap_new.setdefault(tn, {})[yn] = float(n)
                kpis["cap_active_by_tech_year"] = cap_active
                kpis["cap_new_by_tech_year"]    = cap_new

            if "output_cs_y_t" in tables and all(c in columns["output_cs_y_t"] for c in ("cs_id","y_id","pout")):
                cur.execute(""" SELECT cs_id, y_id, MAX(pout) FROM output_cs_y_t GROUP BY cs_id, y_id """)
                peak = {}
                for cs, y, p in cur.fetchall():
                    tn = str(tech_map.get(cs, cs))
                    yn = str(year_map.get(y, y))
                    peak.setdefault(tn, {})[yn] = float(p)
                kpis["peak_pout_by_tech_year"] = peak

            if "output_global" in tables and any(c in columns["output_global"] for c in ("OPEX","CAPEX","TOTEX")):
                cur.execute("SELECT OPEX, CAPEX, TOTEX FROM output_global LIMIT 1")
                row = cur.fetchone()
                if row is not None:
                    kpis["system_cost_totals"] = {"OPEX": float(row[0]) if row[0] is not None else None,
                                                 "CAPEX": float(row[1]) if row[1] is not None else None,
                                                 "TOTEX": float(row[2]) if row[2] is not None else None,}

            out["kpis"] = kpis

        return out


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

