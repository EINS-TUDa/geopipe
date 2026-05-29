# coding: utf-8
import logging
import sqlite3
from dataclasses import field, dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from geopipe.energy_system import EnergySystem, Scenario
from geopipe.energy_system.technology import CHPTechnology
from geopipe.optimization.cesm.techmap import commodity_name
from geopipe.optimization.cesm.units import scale_factors
from geopipe.optimization.solver import Results, Solution

logger = logging.getLogger(__name__)

@dataclass
class CESMSolution(Solution):
    db_path: Optional[Path] = field(default=None)


class CESMResultsParser:
    """Parse a CESM SQLite output DB into a :class:`CESMSolution`."""

    def __init__(self, db_path: Path | str, energy_system: EnergySystem, scenario: Scenario):
        self.db_path = Path(db_path)
        self.energy_system = energy_system
        self.scenario = scenario

    def parse(self) -> CESMSolution:
        with sqlite3.connect(self.db_path) as con:
            output_by_name_year = self._fetch_subprocess_outputs(con)
            opex, capex, totex = self._fetch_global_totals(con)
            emissions_df = self._fetch_emissions(con)

        unit = self.energy_system.units
        factors = scale_factors(unit)
        for cs_map in output_by_name_year.values():
            for year_map in cs_map.values():
                for vals in year_map.values():
                    vals["cap_active"] /= factors["power"]
                    vals["cap_new"] /= factors["power"]
                    vals["eouttot"] /= factors["energy"]
        opex /= factors["money"]
        capex /= factors["money"]
        totex /= factors["money"]
        if not emissions_df.empty:
            emissions_df = emissions_df.assign(amount=emissions_df["amount"] / factors["co2_emissions"])

        results = Results(
            unit=unit,
            opex=opex,
            capex=capex,
            totex=totex,
            emissions_by_year=emissions_df,
            decentral_technologies_per_demand=self._build_decentral(output_by_name_year),
            central_technologies_per_commodity_out=self._build_central(output_by_name_year),
            grids_per_commodity_in=self._build_grids(output_by_name_year),
            pipes_per_commodity_out=self._build_pipes(output_by_name_year),
            imports_per_commodity_out=self._build_imports(output_by_name_year),
            exports_per_commodity_in=self._build_exports(output_by_name_year),
        )

        return CESMSolution(
            energy_system=self.energy_system,
            scenario=self.scenario,
            results=results,
            db_path=self.db_path,
        )

    @staticmethod
    def _fetch_subprocess_outputs(
        con: sqlite3.Connection,
    ) -> dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]]:
        """Return ``{conversion_process_name: {(commodity_in, commodity_out): {year: {cap_active, cap_new, eouttot, newly_installed_units}}}}``.

        Subprocesses are kept distinct so a conversion_process that maps to several subprocesses
        (e.g. a CHP, which has separate subprocesses for each output commodity) can be split apart
        instead of being collapsed into a single per-process row.
        """
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        required = {"output_cs_y", "conversion_subprocess", "conversion_process", "commodity", "year"}
        if not required.issubset(tables):
            return {}
        cur.execute(
            """
            SELECT cp.name,
                   ci.name AS commodity_in,
                   co.name AS commodity_out,
                   y.value AS year,
                   COALESCE(o.cap_active, 0.0) AS cap_active,
                   COALESCE(o.cap_new, 0.0) AS cap_new,
                   COALESCE(o.eouttot, 0.0) AS eouttot,
                   COALESCE(o.newly_installed_units, 0.0) AS newly_installed_units
            FROM output_cs_y AS o
            JOIN conversion_subprocess AS cs ON cs.id = o.cs_id
            JOIN conversion_process AS cp ON cp.id = cs.cp_id
            JOIN commodity AS ci ON ci.id = cs.cin_id
            JOIN commodity AS co ON co.id = cs.cout_id
            JOIN year AS y ON y.id = o.y_id
            """
        )
        result: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]] = {}
        for name, cin, cout, year, cap_active, cap_new, eouttot, newly_installed_units in cur.fetchall():
            result.setdefault(str(name), {}).setdefault((str(cin), str(cout)), {})[int(year)] = {
                "cap_active": cap_active,
                "cap_new": cap_new,
                "eouttot": eouttot,
                "newly_installed_units": newly_installed_units,
            }
        return result

    @staticmethod
    def _fetch_global_totals(con: sqlite3.Connection) -> tuple[float, float, float]:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        if "output_global" not in tables:
            return 0.0, 0.0, 0.0
        cur.execute("SELECT OPEX, CAPEX, TOTEX FROM output_global LIMIT 1")
        row = cur.fetchone()
        if row is None:
            return 0.0, 0.0, 0.0
        return row[0], row[1], row[2]

    @staticmethod
    def _fetch_emissions(con: sqlite3.Connection) -> pd.DataFrame:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        if "output_y" not in tables or "year" not in tables:
            return pd.DataFrame(columns=["year", "amount"])
        cur.execute(
            """
            SELECT y.value AS year, SUM(o.total_annual_co2_emission) AS amount
            FROM output_y AS o
            JOIN year AS y ON y.id = o.y_id
            GROUP BY y.value
            ORDER BY y.value
            """
        )
        rows = [(int(year), amount) for year, amount in cur.fetchall()]
        return pd.DataFrame(rows, columns=["year", "amount"])

    def _rows_for(
        self,
        output_by_name_year: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]],
        cesm_name: str,
        commodity_in: str,
        commodity_out: str,
    ) -> dict[int, dict[str, float]]:
        return output_by_name_year.get(cesm_name, {}).get((commodity_in, commodity_out), {})

    def _build_decentral(
        self,
        output_by_name_year: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]],
    ) -> dict[str, pd.DataFrame]:
        rows: dict[str, list[dict[str, Any]]] = {}
        for region in self.energy_system.regions:
            demand_by_commodity_in: dict[str, str] = {
                d.demand_type.commodity_in: d.name for d in region.demands
            }
            for tech in region.decentral_techs:
                demand_name = demand_by_commodity_in.get(tech.commodity_out)
                if demand_name is None:
                    logger.debug(
                        "Decentral tech '%s' (commodity_out=%s) in region %s has no matching demand; skipping",
                        tech.name, tech.commodity_out, region.id,
                    )
                    continue
                cesm_name = f"{tech.name}_D{region.id}"
                cin = commodity_name(tech.commodity_in, region.id)
                cout = commodity_name(tech.commodity_out, region.id)
                for year, vals in self._rows_for(output_by_name_year, cesm_name, cin, cout).items():
                    rows.setdefault(demand_name, []).append({
                        "year": year,
                        "technology": tech.name,
                        "region_id": region.id,
                        "capacity": vals["cap_active"],
                        "energy_output": vals["eouttot"],
                        "new_capacity": vals["cap_new"],
                    })

        return _frames(rows, ["year", "technology", "region_id", "capacity", "energy_output", "new_capacity"])

    def _build_central(
        self,
        output_by_name_year: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]],
    ) -> dict[str, pd.DataFrame]:
        rows: dict[str, list[dict[str, Any]]] = {}
        for region in self.energy_system.regions:
            for tech in region.central_techs:
                cesm_name = f"{tech.name}_D{region.id}"
                # A CHP maps to multiple subprocesses sharing the same conversion_process_name;
                # each output commodity has its own (cap_active, cap_new, eouttot). The two output
                # subprocesses both have commodity_in == f"Help_{cesm_name}" in the database.
                if isinstance(tech, CHPTechnology):
                    lookups = [
                        (tech.commodity_out, f"Help_{cesm_name}", commodity_name(tech.commodity_out, region.id)),
                        (tech.commodity_out_2, f"Help_{cesm_name}", commodity_name(tech.commodity_out_2, region.id)),
                    ]
                else:
                    lookups = [(
                        tech.commodity_out,
                        commodity_name(tech.commodity_in, region.id),
                        commodity_name(tech.commodity_out, region.id),
                    )]
                for key, cin, cout in lookups:
                    for year, vals in self._rows_for(output_by_name_year, cesm_name, cin, cout).items():
                        rows.setdefault(key, []).append({
                            "year": year,
                            "technology": tech.name,
                            "region_id": region.id,
                            "capacity": vals["cap_active"],
                            "energy_output": vals["eouttot"],
                            "new_capacity": vals["cap_new"],
                            "newly_installed_units": vals["newly_installed_units"],
                        })

        return _frames(
            rows,
            ["year", "technology", "region_id", "capacity", "energy_output", "new_capacity", "newly_installed_units"],
        )

    def _build_grids(
        self,
        output_by_name_year: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]],
    ) -> dict[str, pd.DataFrame]:
        rows: dict[str, list[dict[str, Any]]] = {}
        for region in self.energy_system.regions:
            for grid in region.grids:
                key = grid.commodity_in
                cesm_name = f"{grid.name}_D{region.id}"
                cin = commodity_name(grid.commodity_in, region.id)
                cout = commodity_name(grid.commodity_out, region.id)
                for year, vals in self._rows_for(output_by_name_year, cesm_name, cin, cout).items():
                    rows.setdefault(key, []).append({
                        "year": year,
                        "technology": grid.name,
                        "region_id": region.id,
                        "capacity": vals["cap_active"],
                        "energy_output": vals["eouttot"],
                        "new_capacity": vals["cap_new"],
                    })

        return _frames(rows, ["year", "technology", "region_id", "capacity", "energy_output", "new_capacity"])

    def _build_pipes(
        self,
        output_by_name_year: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]],
    ) -> dict[str, pd.DataFrame]:
        rows: dict[str, list[dict[str, Any]]] = {}
        for pipe in self.energy_system.pipes:
            key = pipe.commodity_out
            cesm_name = f"{pipe.name}_D{pipe.region_id_in}_D{pipe.region_id_out}"
            cin = commodity_name(pipe.commodity_in, pipe.region_id_in)
            cout = commodity_name(pipe.commodity_out, pipe.region_id_out)
            for year, vals in self._rows_for(output_by_name_year, cesm_name, cin, cout).items():
                rows.setdefault(key, []).append({
                    "year": year,
                    "technology": pipe.name,
                    "region_id_from": pipe.region_id_in,
                    "region_id_to": pipe.region_id_out,
                    "capacity": vals["cap_active"],
                    "energy_output": vals["eouttot"],
                    "new_capacity": vals["cap_new"],
                })

        return _frames(
            rows,
            ["year", "technology", "region_id_from", "region_id_to", "capacity", "energy_output", "new_capacity"],
        )

    def _build_imports(
        self,
        output_by_name_year: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]],
    ) -> dict[str, pd.DataFrame]:
        rows: dict[str, list[dict[str, Any]]] = {}
        # Imports are global (commodity_in="Dummy", commodity_out un-prefixed); there is no region.
        for imp in self.energy_system.imports:
            key = imp.commodity_out
            for year, vals in self._rows_for(output_by_name_year, imp.name, "Dummy", imp.commodity_out).items():
                rows.setdefault(key, []).append({
                    "year": year,
                    "capacity": vals["cap_active"],
                    "energy_output": vals["eouttot"],
                    "new_capacity": vals["cap_new"],
                })

        return _frames(rows, ["year", "capacity", "energy_output", "new_capacity"])

    def _build_exports(
        self,
        output_by_name_year: dict[str, dict[tuple[str, str], dict[int, dict[str, float]]]],
    ) -> dict[str, pd.DataFrame]:
        rows: dict[str, list[dict[str, Any]]] = {}
        # Exports are global (commodity_in un-prefixed, commodity_out="Dummy"); there is no region.
        for exp in self.energy_system.exports:
            key = exp.commodity_in
            for year, vals in self._rows_for(output_by_name_year, exp.name, exp.commodity_in, "Dummy").items():
                rows.setdefault(key, []).append({
                    "year": year,
                    "capacity": vals["cap_active"],
                    "energy_input": vals["eouttot"],
                    "new_capacity": vals["cap_new"],
                })

        return _frames(rows, ["year", "capacity", "energy_input", "new_capacity"])


def _frames(
    rows_by_key: dict[str, list[dict[str, Any]]],
    columns: list[str],
) -> dict[str, pd.DataFrame]:
    sort_by = [c for c in ("year", "technology") if c in columns]
    return {
        key: pd.DataFrame(rows, columns=columns).sort_values(sort_by).reset_index(drop=True)
        for key, rows in rows_by_key.items()
    }