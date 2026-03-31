"""CESM result parsing: reads a CESM SQLite DB and returns a CESMResults object.

After the CESM solver writes its output database, this module provides two
functions:

* :func:`backfill_missing_commodity_timeseries` — derives missing
  ``output_co_y_t`` rows from the per-subprocess timeseries table.  Run this
  before parsing so commodity-level KPIs are complete.

* :func:`parse_cesm_outputs` — reads all KPI tables from the SQLite DB and
  returns a :class:`CESMResults` object.

:class:`CESMResults` extends the standardized
:class:`~pypeline.optimization.solver.Results` base class with CESM-specific
fields (emissions, generation/consumption by commodity, per-technology peak
power, etc.).  Future work will standardize these into the base class.
"""
from __future__ import annotations
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from pypeline.optimization.solver import Results

logger = logging.getLogger(__name__)


@dataclass
class CESMResults(Results):
    """Extends the standardized Results with CESM-specific KPI fields.

    The base Results fields (active_capacities, yearly_energy_outputs, opex,
    capex, totex) hold the standardized view. The CESM-specific fields below
    expose additional detail parsed directly from the solver DB.  Future work
    will standardize these into the base Results class.
    """
    emissions_by_year: Dict[str, float] = field(default_factory=dict)
    gen_by_commodity_year: Dict[str, Dict[str, float]] = field(default_factory=dict)
    cons_by_commodity_year: Dict[str, Dict[str, float]] = field(default_factory=dict)
    net_energy_balance_by_year: Dict[str, float] = field(default_factory=dict)
    energy_by_tech_year: Dict[str, Dict[str, float]] = field(default_factory=dict)
    cap_active_by_tech_year: Dict[str, Dict[str, float]] = field(default_factory=dict)
    cap_new_by_tech_year: Dict[str, Dict[str, float]] = field(default_factory=dict)
    peak_pout_by_tech_year: Dict[str, Dict[str, float]] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def backfill_missing_commodity_timeseries(db_path: Path) -> None:
    """Derive missing output_co_y_t rows from the per-subprocess timeseries."""
    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        required = {"output_cs_y_t", "conversion_subprocess", "output_co_y_t"}
        if not required.issubset(tables):
            return
        cur.execute(
            """
            WITH per_kind AS (
                SELECT cs.cout_id AS co_id, o.y_id, o.t_id,
                       SUM(COALESCE(o.eouttime, 0.0)) AS value,
                       1 AS is_gen
                FROM output_cs_y_t AS o
                JOIN conversion_subprocess AS cs ON cs.id = o.cs_id
                GROUP BY cs.cout_id, o.y_id, o.t_id
                HAVING ABS(SUM(COALESCE(o.eouttime, 0.0))) > 1e-9
                UNION ALL
                SELECT cs.cin_id AS co_id, o.y_id, o.t_id,
                       SUM(COALESCE(o.eintime, 0.0)) AS value,
                       0 AS is_gen
                FROM output_cs_y_t AS o
                JOIN conversion_subprocess AS cs ON cs.id = o.cs_id
                GROUP BY cs.cin_id, o.y_id, o.t_id
                HAVING ABS(SUM(COALESCE(o.eintime, 0.0))) > 1e-9
            ),
            aggregated AS (
                SELECT co_id, y_id, t_id,
                       SUM(CASE WHEN is_gen = 1 THEN value ELSE 0 END) AS gen,
                       SUM(CASE WHEN is_gen = 0 THEN value ELSE 0 END) AS cons
                FROM per_kind
                GROUP BY co_id, y_id, t_id
            ),
            missing AS (
                SELECT a.*
                FROM aggregated AS a
                LEFT JOIN output_co_y_t AS existing
                      ON existing.co_id = a.co_id
                     AND existing.y_id = a.y_id
                     AND existing.t_id = a.t_id
                WHERE existing.co_id IS NULL
            )
            INSERT INTO output_co_y_t (co_id, y_id, t_id, enetgen, enetcons)
            SELECT co_id, y_id, t_id, gen, cons FROM missing
            """
        )
        con.commit()


def parse_cesm_outputs(db_path: Path) -> CESMResults:
    """Parse a CESM SQLite result DB into a CESMResults object."""
    raw: Dict[str, Any] = {"status": "ok", "db": str(db_path)}

    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [t[0] for t in cur.fetchall()]
        raw["tables"] = tables

        def pragma_cols(table: str) -> list[str]:
            cur.execute(f"PRAGMA table_info({table})")
            return [row[1] for row in cur.fetchall()]
        columns = {t: pragma_cols(t) for t in tables}
        row_counts: Dict[str, int] = {}
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            row_counts[t] = int(cur.fetchone()[0])
        raw["columns"] = columns
        raw["row_counts"] = row_counts

        def kv(table: str, k: str, v: str) -> dict:
            cur.execute(f"SELECT {k}, {v} FROM {table}")
            return {row[0]: row[1] for row in cur.fetchall()}
        year_map = kv("year", "id", "value") if "year" in tables else {}
        com_map = kv("commodity", "id", "name") if "commodity" in tables else {}

        tech_map: dict[int, str] = {}
        if "conversion_subprocess" in tables and "conversion_process" in tables:
            cur.execute(
                """
                SELECT cs.id, cp.name
                FROM conversion_subprocess cs
                JOIN conversion_process cp ON cs.cp_id = cp.id
                """
            )
            tech_map = {row[0]: row[1] for row in cur.fetchall()}

        kpis: Dict[str, Any] = {}
        if "output_y" in tables and all(c in columns["output_y"] for c in ("y_id", "total_annual_co2_emission")):
            cur.execute("SELECT y_id, SUM(total_annual_co2_emission) FROM output_y GROUP BY y_id")
            kpis["emissions_by_year"] = {str(year_map.get(y, y)): float(v) for (y, v) in cur.fetchall()}

        if "output_co_y_t" in tables and all(c in columns["output_co_y_t"] for c in ("co_id", "y_id", "enetgen", "enetcons")):
            cur.execute(
                "SELECT co_id, y_id, SUM(enetgen) AS gen, SUM(enetcons) AS cons FROM output_co_y_t GROUP BY co_id, y_id"
            )
            gen: Dict[str, Dict[str, float]] = {}
            cons: Dict[str, Dict[str, float]] = {}
            for co, y, g, c in cur.fetchall():
                cn = str(com_map.get(co, co))
                yn = str(year_map.get(y, y))
                gen.setdefault(cn, {})[yn] = float(g)
                cons.setdefault(cn, {})[yn] = float(c)
            kpis["gen_by_commodity_year"] = gen
            kpis["cons_by_commodity_year"] = cons
            bal: Dict[str, float] = {}
            for c, per_year in gen.items():
                for yname, gval in per_year.items():
                    bal[yname] = bal.get(yname, 0.0) + gval
            for c, per_year in cons.items():
                for yname, cval in per_year.items():
                    bal[yname] = bal.get(yname, 0.0) - cval
            kpis["net_energy_balance_by_year"] = bal

        energy_by_tech: Dict[str, Dict[str, float]] = {}
        if "output_cs_y" in tables and all(c in columns["output_cs_y"] for c in ("cs_id", "y_id", "eouttot")):
            cur.execute("SELECT cs_id, y_id, SUM(eouttot) FROM output_cs_y GROUP BY cs_id, y_id")
            for cs, y, val in cur.fetchall():
                tn = str(tech_map.get(cs, cs))
                yn = str(year_map.get(y, y))
                energy_by_tech.setdefault(tn, {})[yn] = float(val)
            kpis["energy_by_tech_year"] = energy_by_tech

        cap_active: Dict[str, Dict[str, float]] = {}
        cap_new: Dict[str, Dict[str, float]] = {}
        if "output_cs_y" in tables and all(c in columns["output_cs_y"] for c in ("cs_id", "y_id", "cap_active", "cap_new")):
            cur.execute("SELECT cs_id, y_id, SUM(cap_active), SUM(cap_new) FROM output_cs_y GROUP BY cs_id, y_id")
            for cs, y, a, n in cur.fetchall():
                tn = str(tech_map.get(cs, cs))
                yn = str(year_map.get(y, y))
                cap_active.setdefault(tn, {})[yn] = float(a)
                cap_new.setdefault(tn, {})[yn] = float(n)
            kpis["cap_active_by_tech_year"] = cap_active
            kpis["cap_new_by_tech_year"] = cap_new

        peak: Dict[str, Dict[str, float]] = {}
        if "output_cs_y_t" in tables and all(c in columns["output_cs_y_t"] for c in ("cs_id", "y_id", "pout")):
            cur.execute("SELECT cs_id, y_id, MAX(pout) FROM output_cs_y_t GROUP BY cs_id, y_id")
            for cs, y, p in cur.fetchall():
                tn = str(tech_map.get(cs, cs))
                yn = str(year_map.get(y, y))
                peak.setdefault(tn, {})[yn] = float(p)
            kpis["peak_pout_by_tech_year"] = peak

        opex: Optional[float] = None
        capex: Optional[float] = None
        totex: Optional[float] = None
        if "output_global" in tables and any(c in columns["output_global"] for c in ("OPEX", "CAPEX", "TOTEX")):
            cur.execute("SELECT OPEX, CAPEX, TOTEX FROM output_global LIMIT 1")
            row = cur.fetchone()
            if row is not None:
                opex = float(row[0]) if row[0] is not None else None
                capex = float(row[1]) if row[1] is not None else None
                totex = float(row[2]) if row[2] is not None else None
                kpis["system_cost_totals"] = {"OPEX": opex, "CAPEX": capex, "TOTEX": totex}

        raw["kpis"] = kpis

    # Build standardized DataFrames
    active_capacities_rows = [
        {"technology": tech, "year": year, "capacity": cap}
        for tech, per_year in cap_active.items()
        for year, cap in per_year.items()
    ]
    active_capacities_df = pd.DataFrame(
        active_capacities_rows,
        columns=["technology", "year", "capacity"],
    ) if active_capacities_rows else pd.DataFrame(columns=["technology", "year", "capacity"])

    energy_output_rows = [
        {"technology": tech, "year": year, "energy_output": val}
        for tech, per_year in energy_by_tech.items()
        for year, val in per_year.items()
    ]
    yearly_energy_outputs_df = pd.DataFrame(
        energy_output_rows,
        columns=["technology", "year", "energy_output"],
    ) if energy_output_rows else pd.DataFrame(columns=["technology", "year", "energy_output"])

    return CESMResults(
        active_capacities=active_capacities_df,
        yearly_energy_outputs=yearly_energy_outputs_df,
        opex=opex,
        capex=capex,
        totex=totex,
        emissions_by_year=kpis.get("emissions_by_year", {}),
        gen_by_commodity_year=kpis.get("gen_by_commodity_year", {}),
        cons_by_commodity_year=kpis.get("cons_by_commodity_year", {}),
        net_energy_balance_by_year=kpis.get("net_energy_balance_by_year", {}),
        energy_by_tech_year=kpis.get("energy_by_tech_year", {}),
        cap_active_by_tech_year=kpis.get("cap_active_by_tech_year", {}),
        cap_new_by_tech_year=kpis.get("cap_new_by_tech_year", {}),
        peak_pout_by_tech_year=kpis.get("peak_pout_by_tech_year", {}),
        raw=raw,
    )
