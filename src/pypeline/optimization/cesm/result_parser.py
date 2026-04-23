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
import math
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from pypeline.optimization.solver import Results

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _year_gap_for(years: list[int], year: int) -> int:
    try:
        idx = years.index(year)
    except ValueError:
        return 1
    if idx >= len(years) - 1:
        return 1
    return int(years[idx + 1] - year)


def _discount_factor_for(start_year: int, discount_rate: float, year: int) -> float:
    return float((1.0 + float(discount_rate)) ** float(start_year - year))


def _estimate_installed_units_for_base_cost(
    *,
    cap_new: float,
    cap_max: float,
    cap_min: float,
) -> float:
    if cap_new <= 1e-12:
        return 0.0
    if math.isfinite(cap_max) and cap_max > 1e-12:
        return float(max(1, int(math.ceil((cap_new / cap_max) - 1e-12))))
    if math.isfinite(cap_min) and cap_min > 1e-12:
        return float(max(1, int(math.ceil((cap_new / cap_min) - 1e-12))))
    return 1.0


def _compute_objective_cost_kpis(
    *,
    con: sqlite3.Connection,
    tables: list[str],
    columns: dict[str, list[str]],
    kpis: Dict[str, Any],
) -> None:
    required_tables = {"param_cs_y", "conversion_subprocess", "conversion_process", "year", "param_global"}
    if not required_tables.issubset(set(tables)):
        return
    needed_param_cols = {
        "cs_id",
        "y_id",
        "opex_cost_energy",
        "opex_cost_power",
        "capex_cost_power",
        "capex_cost_base",
        "cap_max",
        "cap_min",
    }
    if not needed_param_cols.issubset(set(columns.get("param_cs_y", []))):
        return
    output_cols = set(columns.get("output_cs_y", []))
    has_output_cs_y = "output_cs_y" in tables and {"cs_id", "y_id", "cap_new", "cap_active", "eouttot"}.issubset(output_cols)

    cur = con.cursor()
    cur.execute("SELECT value FROM year ORDER BY value")
    years = [int(row[0]) for row in cur.fetchall()]
    if not years:
        return

    cur.execute("SELECT discount_rate FROM param_global LIMIT 1")
    row = cur.fetchone()
    discount_rate = _safe_float(row[0] if row else 0.0)
    start_year = int(years[0])

    if has_output_cs_y:
        dis_salvage_expr = "COALESCE(o.dis_salvage_value, 0.0)"
        if "dis_salvage_value" not in output_cols:
            dis_salvage_expr = "0.0"
        cur.execute(
            f"""
            SELECT cp.name,
                   y.value,
                   COALESCE(o.cap_new, 0.0) AS cap_new,
                   COALESCE(o.cap_active, 0.0) AS cap_active,
                   COALESCE(o.eouttot, 0.0) AS eouttot,
                   {dis_salvage_expr} AS dis_salvage_value,
                   p.opex_cost_energy,
                   p.opex_cost_power,
                   p.capex_cost_power,
                   p.capex_cost_base,
                   p.cap_max,
                   p.cap_min
            FROM param_cs_y AS p
            JOIN conversion_subprocess AS cs ON cs.id = p.cs_id
            JOIN conversion_process AS cp ON cp.id = cs.cp_id
            JOIN year AS y ON y.id = p.y_id
            LEFT JOIN output_cs_y AS o ON o.cs_id = p.cs_id AND o.y_id = p.y_id
            """
        )
    else:
        cur.execute(
            """
            SELECT cp.name,
                   y.value,
                   0.0 AS cap_new,
                   0.0 AS cap_active,
                   0.0 AS eouttot,
                   0.0 AS dis_salvage_value,
                   p.opex_cost_energy,
                   p.opex_cost_power,
                   p.capex_cost_power,
                   p.capex_cost_base,
                   p.cap_max,
                   p.cap_min
            FROM param_cs_y AS p
            JOIN conversion_subprocess AS cs ON cs.id = p.cs_id
            JOIN conversion_process AS cp ON cp.id = cs.cp_id
            JOIN year AS y ON y.id = p.y_id
            """
        )
    rows = cur.fetchall()
    if not rows:
        return

    by_tech_year: Dict[str, Dict[str, Dict[str, float]]] = {}
    by_year: Dict[str, Dict[str, float]] = {}
    by_year_dhn: Dict[str, Dict[str, float]] = {}

    def _ensure_bucket(container: Dict[str, Dict[str, float]], year_key: str) -> Dict[str, float]:
        bucket = container.get(year_key)
        if bucket is None:
            bucket = {
                "opex": 0.0,
                "capex_gross": 0.0,
                "capex_net": 0.0,
                "totex": 0.0,
            }
            container[year_key] = bucket
        return bucket

    for (
        cp_name,
        year_raw,
        cap_new_raw,
        cap_active_raw,
        eouttot_raw,
        dis_salvage_raw,
        opex_energy_raw,
        opex_power_raw,
        capex_power_raw,
        capex_base_raw,
        cap_max_raw,
        cap_min_raw,
    ) in rows:
        tech = str(cp_name)
        year = int(year_raw)
        year_key = str(year)
        cap_new = _safe_float(cap_new_raw)
        cap_active = _safe_float(cap_active_raw)
        eouttot = _safe_float(eouttot_raw)
        dis_salvage = _safe_float(dis_salvage_raw)
        opex_energy = _safe_float(opex_energy_raw)
        opex_power = _safe_float(opex_power_raw)
        capex_power = _safe_float(capex_power_raw)
        capex_base = _safe_float(capex_base_raw)
        cap_max = _safe_float(cap_max_raw)
        cap_min = _safe_float(cap_min_raw)

        discount_factor = _discount_factor_for(start_year, discount_rate, year)
        year_gap = _year_gap_for(years, year)

        opex_obj = (cap_active * opex_power + eouttot * opex_energy) * float(year_gap) * discount_factor
        capex_gross_obj = cap_new * capex_power * discount_factor
        if capex_base > 0.0:
            units_est = _estimate_installed_units_for_base_cost(
                cap_new=cap_new,
                cap_max=cap_max,
                cap_min=cap_min,
            )
            capex_gross_obj += units_est * capex_base * discount_factor
        capex_net_obj = capex_gross_obj - dis_salvage
        totex_obj = opex_obj + capex_net_obj

        tech_map = by_tech_year.setdefault(tech, {})
        tech_bucket = tech_map.get(year_key)
        if tech_bucket is None:
            tech_bucket = {
                "opex": 0.0,
                "capex_gross": 0.0,
                "capex_net": 0.0,
                "totex": 0.0,
            }
            tech_map[year_key] = tech_bucket
        tech_bucket["opex"] += float(opex_obj)
        tech_bucket["capex_gross"] += float(capex_gross_obj)
        tech_bucket["capex_net"] += float(capex_net_obj)
        tech_bucket["totex"] += float(totex_obj)

        year_bucket = _ensure_bucket(by_year, year_key)
        year_bucket["opex"] += float(opex_obj)
        year_bucket["capex_gross"] += float(capex_gross_obj)
        year_bucket["capex_net"] += float(capex_net_obj)
        year_bucket["totex"] += float(totex_obj)

        if tech.startswith("HeatExchanger") or tech.startswith("heat_grid") or tech.startswith("Pipe_D"):
            year_dhn_bucket = _ensure_bucket(by_year_dhn, year_key)
            year_dhn_bucket["opex"] += float(opex_obj)
            year_dhn_bucket["capex_gross"] += float(capex_gross_obj)
            year_dhn_bucket["capex_net"] += float(capex_net_obj)
            year_dhn_bucket["totex"] += float(totex_obj)

    kpis["objective_cost_by_tech_year"] = by_tech_year
    kpis["objective_cost_totals_by_year"] = by_year
    kpis["objective_cost_dhn_by_year"] = by_year_dhn


def _capacity_free_connector_cs_ids(
    *,
    con: sqlite3.Connection,
    tables: list[str],
    columns: dict[str, list[str]],
) -> set[int]:
    """Return subprocess ids whose capacity variables are non-informative connector artifacts.

    These rows are unbounded (no cap_max) and carry no capacity economics or
    residual/lower-bound constraints, so solver-degenerate cap_active values
    (for example 1.0) should not be reported as meaningful capacity.
    """
    if "param_cs_y" not in tables:
        return set()
    needed = {
        "cs_id",
        "cap_max",
        "cap_min",
        "cap_res_min",
        "cap_res_max",
        "capex_cost_power",
        "capex_cost_base",
        "opex_cost_power",
    }
    if not needed.issubset(set(columns.get("param_cs_y", []))):
        return set()

    cur = con.cursor()
    cur.execute(
        """
        SELECT p.cs_id
        FROM param_cs_y AS p
        GROUP BY p.cs_id
        HAVING
            MAX(ABS(COALESCE(p.cap_max, 0.0))) = 0
            AND SUM(CASE WHEN p.cap_max IS NULL THEN 1 ELSE 0 END) > 0
            AND MAX(ABS(COALESCE(p.cap_min, 0.0))) = 0
            AND MAX(ABS(COALESCE(p.cap_res_min, 0.0))) = 0
            AND MAX(ABS(COALESCE(p.cap_res_max, 0.0))) = 0
            AND MAX(ABS(COALESCE(p.capex_cost_power, 0.0))) = 0
            AND MAX(ABS(COALESCE(p.capex_cost_base, 0.0))) = 0
            AND MAX(ABS(COALESCE(p.opex_cost_power, 0.0))) = 0
        """
    )
    return {int(row[0]) for row in cur.fetchall()}


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
        synthetic_capacity_cs_ids = _capacity_free_connector_cs_ids(
            con=con,
            tables=tables,
            columns=columns,
        )
        if "output_cs_y" in tables and all(c in columns["output_cs_y"] for c in ("cs_id", "y_id", "cap_active", "cap_new")):
            cur.execute("SELECT cs_id, y_id, SUM(cap_active), SUM(cap_new) FROM output_cs_y GROUP BY cs_id, y_id")
            for cs, y, a, n in cur.fetchall():
                if int(cs) in synthetic_capacity_cs_ids:
                    continue
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

        _compute_objective_cost_kpis(con=con, tables=tables, columns=columns, kpis=kpis)

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
